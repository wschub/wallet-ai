"""POST /chat/stream - chat del asistente sobre SSE (tramo C, T043).

- Cuerpo: {message (1..1000), previous_user_messages (<=5 strings),
  selected_recipient_id (handle opcional)} con extra='forbid': sin roles
  assistant ni tool ni campos de identidad.
- Cualquier rol autenticado puede chatear; los permisos finos los aplica
  el registro de herramientas.
- El ToolContext se construye AQUI a partir del JWT y del trace_id del
  middleware: el orquestador no conoce tokens.
- Primer evento: trace con el trace_id del servidor; el stream termina
  siempre con done o error; los errores son genericos.
- Si el cliente se desconecta, el turno se cancela limpiamente y se audita
  response con estado cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from wallet_ai.application.assistant.orchestrator import (
    GENERIC_ERROR_MESSAGE,
    run_turn,
)
from wallet_ai.application.assistant.tools import ToolContext, ToolDeps, build_default_registry
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.account_read_repository import AccountReadRepository
from wallet_ai.infrastructure.db.draft_repository import DraftRepository
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor
from wallet_ai.infrastructure.pii.masker import PIIMasker
from wallet_ai.infrastructure.providers.fake.llm import FakeLLM
from wallet_ai.infrastructure.tracing.logger import StructuredLogger
from wallet_ai.interfaces.api.deps.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])
logger = StructuredLogger()


class ChatRequest(BaseModel):
    """Sin roles assistant/tool: solo texto del usuario y un handle opaco."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=1000)
    previous_user_messages: list[str] = Field(default_factory=list, max_length=5)
    selected_recipient_id: str | None = None


def _sse(event) -> dict:
    return {"event": event.type, "data": json.dumps(event.payload(), ensure_ascii=False)}


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    user=Depends(get_current_user),
):
    trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex

    raw = user.get("sub") or user.get("id")
    try:
        user_id = UUID(str(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="bad user") from None

    # identidad desde el JWT; el orquestador no la conoce
    ctx = ToolContext(user_id=user_id, role=str(user.get("role") or ""), trace_id=trace_id)

    # (a) el mensaje pasa por PII antes de llegar a los logs
    pii = PIIMasker()
    logger.info(
        "chat turn started",
        extra={"message": pii.mask(payload.message), "role": ctx.role},
    )

    settings = Settings()
    conn = await asyncpg.connect(settings.asyncpg_dsn)
    auditor = ConnectionToolCallAuditor(conn)
    deps = ToolDeps(
        accounts=AccountReadRepository(conn),
        drafts=DraftRepository(conn),
        recipients=RecipientRepository(conn),
        auditor=auditor,
    )
    turn = run_turn(
        ctx=ctx,
        message=payload.message,
        registry=build_default_registry(),
        deps=deps,
        llm=FakeLLM(),
        pii=pii,
        auditor=auditor,
        previous_user_messages=payload.previous_user_messages,
        selected_recipient_id=payload.selected_recipient_id,
        llm_timeout=settings.LLM_TIMEOUT_SECONDS,
    )

    async def _events():
        try:
            async for event in turn:
                yield _sse(event)
        except Exception:
            # error generico: el stream termina siempre con done o error
            logger.exception("chat turn crashed")
            yield {"event": "error", "data": json.dumps({"detail": GENERIC_ERROR_MESSAGE})}
        finally:
            with contextlib.suppress(Exception):
                await turn.aclose()
            # el cierre viaja en una tarea nueva del loop: tras una
            # desconexion el await de limpieza seria re-cancelado por el
            # scope cancelado y la conexion quedaria abierta
            asyncio.get_running_loop().create_task(conn.close())

    async def _on_client_disconnect(message: dict | None = None) -> None:
        # sse-starlette espera este handler ANTES de cancelar el task group:
        # es el unico punto fiable para la auditoria de la desconexion.
        try:
            await turn.aclose()
            # cierre limpio: el GeneratorExit del orquestador audito
            # 'cancelled' por si mismo
            return
        except Exception:
            # el turno esta ejecutando una llamada (aclose sobre un generador
            # en marcha lanza RuntimeError): la cancelacion del stream lo
            # detendra, pero su finally no puede esperar dentro de un scope
            # ya cancelado, asi que la fila response/cancelled la escribimos
            # aqui
            pass
        # con una conexion PROPIA: la del turno puede quedar abortada por la
        # operacion cancelada en vuelo y no sirve para escribir
        try:
            audit_conn = await asyncpg.connect(settings.asyncpg_dsn)
        except Exception:
            return
        try:
            await ConnectionToolCallAuditor(audit_conn).record_event(
                trace_id=trace_id,
                user_id=user_id,
                event_type="response",
                payload={"status": "cancelled", "message_length": 0},
            )
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                await audit_conn.close()

    return EventSourceResponse(
        _events(),
        headers={"X-Trace-Id": trace_id},
        client_close_handler_callable=_on_client_disconnect,
    )
