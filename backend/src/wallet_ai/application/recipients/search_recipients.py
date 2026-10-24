"""Caso de uso: buscar destinatarios y emitir un handle por resultado.

Reglas:
- minimo 3 caracteres (sin contar espacios sobrantes);
- maximo 5 resultados;
- solo usuarios con rol `cliente` y cuenta no sistémica (lo aplica el repo);
- excluye al propio solicitante (lo aplica el repo);
- cada resultado emite un handle NUEVO, ligado al solicitante;
- devuelve el nombre enmascarado, sin correo ni ids de usuario/cuenta;
- escribe un evento de auditoria `tool_call` con `trace_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from wallet_ai.domain.ports.audit import ToolCallAuditor
from wallet_ai.domain.ports.recipients import RecipientRepositoryPort
from wallet_ai.domain.recipients.candidate import RecipientResult
from wallet_ai.domain.recipients.errors import InvalidSearchQuery
from wallet_ai.domain.recipients.masking import mask_display_name

MIN_QUERY_LENGTH = 3
MAX_RESULTS = 5
HANDLE_TTL = timedelta(minutes=15)


async def search_recipients(
    recipients: RecipientRepositoryPort,
    auditor: ToolCallAuditor,
    *,
    requester_user_id: UUID,
    query: str,
    trace_id: str,
    now: datetime | None = None,
) -> list[RecipientResult]:
    normalized = (query or "").strip()
    if len(normalized) < MIN_QUERY_LENGTH:
        raise InvalidSearchQuery(f"query must have at least {MIN_QUERY_LENGTH} characters")
    if not trace_id:
        raise ValueError("trace_id is required to audit a tool call")

    now = now or datetime.now(UTC)
    candidates = await recipients.search(
        requester_user_id=requester_user_id, query=normalized, limit=MAX_RESULTS
    )

    results: list[RecipientResult] = []
    for candidate in candidates[:MAX_RESULTS]:
        record = await recipients.issue_handle(
            requester_user_id=requester_user_id,
            target_account_id=candidate.account_id,
            expires_at=now + HANDLE_TTL,
        )
        results.append(
            RecipientResult(
                recipient_id=record.handle,
                name_masked=mask_display_name(candidate.display_name),
                expires_at=record.expires_at,
            )
        )

    await auditor.record_tool_call(
        trace_id=trace_id,
        user_id=requester_user_id,
        payload={
            "tool": "find_recipient",
            "query": normalized,
            "results": len(results),
        },
    )
    return results
