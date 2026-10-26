"""Orquestador del asistente (tramo C, T038).

Generador asincrono de eventos tipados: trace, tool, message, draft,
clarification, done y error. Todas las reglas del turno son deterministas y
viven AQUI (nunca dependen de lo que diga el modelo):

- El mensaje del usuario pasa por el servicio de PII antes de llegar al LLM,
  a los logs y a la auditoria.
- Lista blanca: solo se ejecutan herramientas del registro; una llamada a un
  nombre ausente (confirm_draft, execute_transfer, ...) se bloquea, se
  audita como guardrail_block y el turno continua con un mensaje seguro.
- Limites: maximo 4 iteraciones del bucle de herramientas, 6 llamadas por
  turno, timeout por llamada al LLM (configurable) y mensaje de hasta 1000
  caracteres. Al agotarlos el turno termina con un error generico.
- Los resultados de herramientas llegan al LLM como datos en un sobre JSON
  con rol 'tool', nunca concatenados a instrucciones.
- El evento draft sale SIEMPRE del resultado de draft_transfer, nunca del
  texto del modelo; tras crear un borrador se anade una frase fija del
  servidor. Si el texto afirma que se movio dinero estando solo el borrador,
  se reemplaza por la plantilla y se audita como guardrail_block.
- Ambiguedad: si find_recipient devuelve mas de un candidato sin
  seleccion del usuario, el turno termina con un evento clarification y
  cualquier draft_transfer queda bloqueado. El modelo nunca elige entre
  candidatos.
- Auditoria: llm_call (proveedor, modelo, longitudes; sin texto), tool_call
  (lo escribe el registro), guardrail_block y response (longitud y estado),
  todo con trace_id y asegurando la fila de interactions antes.

Aislamiento de la confirmacion: este modulo no conoce el ledger ni la
confirmacion de borradores; jamas importa `application.ledger`,
`confirm_draft` ni `ledger_repository`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from wallet_ai.application.assistant.tools.context import ToolContext
from wallet_ai.application.assistant.tools.errors import (
    ToolNotFound,
    ToolPermissionDenied,
    ToolValidationError,
)
from wallet_ai.application.assistant.tools.registry import ToolDeps, ToolRegistry
from wallet_ai.domain.ports.audit import AssistantTurnAuditor
from wallet_ai.domain.ports.llm import LLMProvider
from wallet_ai.domain.ports.pii_service import PIIService

MAX_LOOP_ITERATIONS = 4
MAX_TOOL_CALLS_PER_TURN = 6
MAX_MESSAGE_CHARS = 1000
DEFAULT_LLM_TIMEOUT_SECONDS = 10.0

GENERIC_ERROR_MESSAGE = "No pude completar la solicitud. Inténtalo de nuevo."
DRAFT_CREATED_PHRASE = "Borrador creado: confírmalo en la app. Aún no se movió dinero."
DRAFT_ONLY_TEMPLATE = (
    "Preparé un borrador de la transferencia. "
    "Borrador creado: confírmalo en la app. Aún no se movió dinero."
)
NO_TRANSFER_TEMPLATE = (
    "No se realizó ninguna transferencia. Si quieres enviar dinero, "
    "pídeme un borrador y confírmalo en la app."
)

SYSTEM_PROMPT = (
    "Eres el asistente financiero de la app. Solo puedes usar las herramientas "
    "del registro. Nunca mueves ni confirmas transferencias: solo creas borradores "
    "que el usuario confirma en la app. Responde en español y de forma breve."
)

# Afirmaciones de que el dinero ya se movio (el chat JAMAS mueve dinero, asi
# que cualquiera de ellas es falsa). La frase fija del servidor ("Aún no se
# movió dinero") esta protegida por el lookbehind de negacion.
_COMPLETION_CLAIM_RE = re.compile(
    r"(?i)\b(?:transfer[ií]|transfiri[oó]|transferid[oa]s?|enviad[oa]s?|envi[eé]|"
    r"completad[oa]s?|complet[eé]|complet[oó]|confirmad[oa]s?|confirm[eé]|confirm[oó]|"
    r"ya\s+est[aá]\s+hecho|(?<!no\s)se\s+movi[oó]|(?<!no\s)se\s+realiz[oó]|"
    r"realizad[oa]s?|exitos[oa]s?|done|sent|confirmed)\b"
)

# Codigos seguros para los sobres de error (nunca texto de excepciones)
_TOOL_FAILED = "tool_failed"
_TOOL_NOT_AVAILABLE = "tool_not_available"
_PERMISSION_DENIED = "permission_denied"
_INVALID_ARGUMENTS = "invalid_arguments"
_CLARIFICATION_REQUIRED = "clarification_required"


# --------------------------------------------------------------------------
# eventos tipados
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceEvent:
    trace_id: str
    type: ClassVar[str] = "trace"

    def payload(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id}


@dataclass(frozen=True)
class ToolEvent:
    tool: str
    status: str
    type: ClassVar[str] = "tool"

    def payload(self) -> dict[str, Any]:
        return {"tool": self.tool, "status": self.status}


@dataclass(frozen=True)
class MessageEvent:
    content: str
    type: ClassVar[str] = "message"

    def payload(self) -> dict[str, Any]:
        return {"content": self.content}


@dataclass(frozen=True)
class DraftEvent:
    """Sale SIEMPRE del resultado de draft_transfer, nunca del texto."""

    draft_id: str
    amount: str
    recipient_name_masked: str
    expires_at: str
    type: ClassVar[str] = "draft"

    def payload(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "amount": self.amount,
            "recipient_name_masked": self.recipient_name_masked,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ClarificationEvent:
    """Opciones numeradas con handle y nombre enmascarado."""

    options: tuple[dict[str, Any], ...]
    type: ClassVar[str] = "clarification"

    def payload(self) -> dict[str, Any]:
        return {"options": [dict(option) for option in self.options]}


@dataclass(frozen=True)
class DoneEvent:
    type: ClassVar[str] = "done"

    def payload(self) -> dict[str, Any]:
        return {}


@dataclass(frozen=True)
class ErrorEvent:
    detail: str = GENERIC_ERROR_MESSAGE
    type: ClassVar[str] = "error"

    def payload(self) -> dict[str, Any]:
        return {"detail": self.detail}


AssistantEvent = (
    TraceEvent | ToolEvent | MessageEvent | DraftEvent | ClarificationEvent | DoneEvent | ErrorEvent
)


# --------------------------------------------------------------------------
# turno
# --------------------------------------------------------------------------


def _tool_envelope(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Sobre JSON con rol 'tool': datos, nunca instrucciones."""
    return {
        "role": "tool",
        "content": json.dumps({"tool": name, "data": data}, ensure_ascii=False, default=str),
    }


def _tool_schemas(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_model.model_json_schema(),
        }
        for tool in registry.list_tools()
    ]


async def run_turn(
    *,
    ctx: ToolContext,
    message: str,
    registry: ToolRegistry,
    deps: ToolDeps,
    llm: LLMProvider,
    pii: PIIService,
    auditor: AssistantTurnAuditor,
    previous_user_messages: Sequence[str] = (),
    selected_recipient_id: str | None = None,
    llm_timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> AsyncIterator[AssistantEvent]:
    """Ejecuta un turno completo del asistente y emite sus eventos.

    La identidad (`ToolContext`) la construye SIEMPRE quien invoca: este
    modulo no conoce JWT ni tokens.
    """
    status = "error"
    message_length = 0
    response_audited = False
    cancelled_by_close = False
    messages: list[dict[str, Any]] = []

    async def _audit(event_type: str, payload: dict[str, Any]) -> None:
        await auditor.record_event(
            trace_id=ctx.trace_id, user_id=ctx.user_id, event_type=event_type, payload=payload
        )

    try:
        yield TraceEvent(ctx.trace_id)

        # (a) PII antes que todo: LLM, logs y auditoria ven el texto
        # enmascarado; el mensaje en crudo no sale de aqui.
        masked = pii.mask(message or "")

        # (c) mensaje de hasta 1000 caracteres
        if len(message) > MAX_MESSAGE_CHARS:
            await _audit(
                "guardrail_block",
                {"reason": "message_too_long", "length": len(message)},
            )
            yield ErrorEvent()
            return

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for previous in previous_user_messages:
            messages.append({"role": "user", "content": pii.mask(previous or "")})
        messages.append({"role": "user", "content": masked})

        schemas = _tool_schemas(registry)
        iterations = 0
        tool_calls_used = 0
        draft: dict[str, Any] | None = None
        candidates: list[dict[str, Any]] = []
        ambiguous = False

        while True:
            # (c) maximo 4 iteraciones del bucle de herramientas
            if iterations >= MAX_LOOP_ITERATIONS:
                await _audit("guardrail_block", {"reason": "iteration_limit"})
                yield ErrorEvent()
                return
            iterations += 1

            # (g) llm_call sin texto: proveedor, modelo y longitudes
            await _audit(
                "llm_call",
                {
                    "provider": llm.provider_name,
                    "model": llm.model_name,
                    "iteration": iterations,
                    "message_count": len(messages),
                    "content_lengths": [len(str(m.get("content", ""))) for m in messages],
                },
            )
            try:
                response = await asyncio.wait_for(
                    llm.chat(
                        messages,
                        tools=schemas,
                        selected_recipient_id=selected_recipient_id,
                    ),
                    timeout=llm_timeout,
                )
            except TimeoutError:
                yield ErrorEvent()
                return

            if not response.tool_calls:
                content = response.content or ""
                if draft is not None:
                    # (e) el modelo no puede afirmar que se movio dinero:
                    # la plantilla del servidor reemplaza el texto
                    if _COMPLETION_CLAIM_RE.search(content):
                        await _audit(
                            "guardrail_block",
                            {"reason": "completion_claim", "draft_id": draft.get("draft_id")},
                        )
                        content = DRAFT_ONLY_TEMPLATE
                    elif DRAFT_CREATED_PHRASE not in content:
                        content = f"{content} {DRAFT_CREATED_PHRASE}".strip()
                elif _COMPLETION_CLAIM_RE.search(content):
                    await _audit("guardrail_block", {"reason": "completion_claim"})
                    content = NO_TRANSFER_TEMPLATE
                message_length = len(content)
                yield MessageEvent(content)
                yield DoneEvent()
                status = "ok"
                return

            for call in response.tool_calls:
                # (c) maximo 6 llamadas por turno
                if tool_calls_used >= MAX_TOOL_CALLS_PER_TURN:
                    await _audit("guardrail_block", {"reason": "tool_call_limit"})
                    yield ErrorEvent()
                    return
                tool_calls_used += 1

                # (b) lista blanca: solo herramientas del registro
                try:
                    registry.get(call.name)
                except ToolNotFound:
                    await _audit(
                        "guardrail_block",
                        {"reason": "unknown_tool", "tool": call.name},
                    )
                    yield ToolEvent(call.name, "blocked")
                    messages.append(
                        _tool_envelope(call.name, {"error": _TOOL_NOT_AVAILABLE})
                    )
                    continue

                # (f) el modelo no elige entre candidatos ambiguos y, con
                # seleccion del usuario, solo puede usar el handle elegido
                if call.name == "draft_transfer":
                    if ambiguous:
                        await _audit(
                            "guardrail_block",
                            {"reason": "ambiguous_recipient", "tool": call.name},
                        )
                        yield ToolEvent(call.name, "blocked")
                        messages.append(
                            _tool_envelope(call.name, {"error": _CLARIFICATION_REQUIRED})
                        )
                        continue
                    if (
                        selected_recipient_id is not None
                        and call.arguments.get("recipient_id") != selected_recipient_id
                    ):
                        await _audit(
                            "guardrail_block",
                            {"reason": "recipient_not_selected", "tool": call.name},
                        )
                        yield ToolEvent(call.name, "blocked")
                        messages.append(
                            _tool_envelope(call.name, {"error": _CLARIFICATION_REQUIRED})
                        )
                        continue

                try:
                    result = await registry.invoke(call.name, ctx, call.arguments, deps)
                except ToolValidationError:
                    # el registro ya audito el tool_call invalido
                    yield ToolEvent(call.name, "error")
                    messages.append(
                        _tool_envelope(call.name, {"error": _INVALID_ARGUMENTS})
                    )
                    continue
                except ToolPermissionDenied:
                    yield ToolEvent(call.name, "error")
                    messages.append(
                        _tool_envelope(call.name, {"error": _PERMISSION_DENIED})
                    )
                    continue
                except Exception:
                    # error de dominio: sobre seguro sin detalles internos
                    yield ToolEvent(call.name, "error")
                    messages.append(_tool_envelope(call.name, {"error": _TOOL_FAILED}))
                    continue

                yield ToolEvent(call.name, "ok")
                messages.append(_tool_envelope(call.name, result))

                if call.name == "find_recipient":
                    candidates = list(result.get("results") or [])
                    if len(candidates) > 1 and selected_recipient_id is None:
                        ambiguous = True
                elif call.name == "draft_transfer":
                    draft = result
                    # (e) el evento draft sale SOLO del resultado de la
                    # herramienta: id, monto como string, nombre enmascarado
                    # del destinatario y expires_at
                    yield DraftEvent(
                        draft_id=str(result["draft_id"]),
                        amount=str(result["amount"]),
                        recipient_name_masked=str(
                            result.get("recipient_name_masked") or "***"
                        ),
                        expires_at=str(result["expires_at"]),
                    )

            if ambiguous:
                # (f) termina el turno con la clarificacion numerada
                yield ClarificationEvent(
                    options=tuple(
                        {
                            "n": index + 1,
                            "recipient_id": candidate["recipient_id"],
                            "name_masked": candidate["name_masked"],
                        }
                        for index, candidate in enumerate(candidates)
                    )
                )
                yield DoneEvent()
                status = "ok"
                return
    except GeneratorExit:
        # el cliente se desconecto y quien sirve el turno cerro el generador
        # (aclose): cancelar limpiamente y auditar aqui, que este camino es
        # determinista
        status = "cancelled"
        cancelled_by_close = True
        raise
    except asyncio.CancelledError:
        # cancelacion del task anfitrion (p. ej. sse-starlette cortando el
        # stream): los await dentro de un scope cancelado se re-cancelan, asi
        # que la fila response/cancelled la escribe quien cierra el turno (el
        # handler de desconexion del router)
        status = "cancelled"
        raise
    finally:
        if not response_audited:
            response_audited = True
            # (g) response: longitud y estado, siempre con el trace_id y la
            # fila de interactions garantizada antes (record_event la crea)
            if status == "cancelled" and not cancelled_by_close:
                pass  # audita el dueño del cierre (ver comentario arriba)
            elif status == "cancelled":
                with contextlib.suppress(Exception):
                    await _audit(
                        "response", {"status": status, "message_length": message_length}
                    )
            else:
                await _audit(
                    "response", {"status": status, "message_length": message_length}
                )
