"""Endpoint de destinatarios.

- GET /recipients?q=...  -> busca por nombre (minimo 3 caracteres), devuelve
  handles opacos y nombres enmascarados. Solo rol cliente.

No expone correos, ids de usuario ni ids de cuenta. Toda respuesta lleva
`X-Trace-Id` (lo añade el middleware).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wallet_ai.application.recipients.search_recipients import search_recipients
from wallet_ai.domain.recipients.errors import InvalidSearchQuery
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor
from wallet_ai.interfaces.api.deps.roles import require_role

router = APIRouter(prefix="/recipients", tags=["recipients"])


async def _get_conn():
    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


def _user_uuid(user: dict) -> UUID:
    raw = user.get("sub") or user.get("id")
    try:
        return UUID(str(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="bad user")


def _trace_id(request: Request) -> str:
    # el trace_id lo fija el middleware (siempre generado en el servidor);
    # nunca se lee del header de la peticion
    return getattr(request.state, "trace_id", None) or ""


@router.get("")
async def search(
    request: Request,
    q: str = Query(..., description="Nombre a buscar (minimo 3 caracteres)"),
    user=Depends(require_role("cliente")),
    conn=Depends(_get_conn),
):
    try:
        async with conn.transaction():
            results = await search_recipients(
                RecipientRepository(conn),
                ConnectionToolCallAuditor(conn),
                requester_user_id=_user_uuid(user),
                query=q,
                trace_id=_trace_id(request),
            )
    except InvalidSearchQuery:
        raise HTTPException(status_code=400, detail="query must have at least 3 characters")

    return {
        "items": [
            {
                "recipient_id": r.recipient_id,
                "name_masked": r.name_masked,
                "expires_at": r.expires_at.isoformat(),
            }
            for r in results
        ],
        "next_cursor": None,
    }
