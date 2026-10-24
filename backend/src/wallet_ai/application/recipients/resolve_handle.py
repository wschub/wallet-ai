"""Caso de uso: resolver un handle a la cuenta destino interna.

Un handle desconocido, vencido o emitido a otro solicitante produce SIEMPRE el
mismo error generico (`HandleNotResolvable`), sin decir cual de los casos fue.
Un UUID de cuenta usado como handle tambien se rechaza con ese mismo error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from wallet_ai.domain.ports.recipients import RecipientRepositoryPort
from wallet_ai.domain.recipients.errors import HandleNotResolvable


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


async def resolve_handle(
    recipients: RecipientRepositoryPort,
    *,
    requester_user_id: UUID,
    handle: str,
    now: datetime | None = None,
) -> UUID:
    candidate = (handle or "").strip()
    if not candidate or _looks_like_uuid(candidate):
        raise HandleNotResolvable()

    record = await recipients.find(
        requester_user_id=requester_user_id, handle=candidate
    )
    now = now or datetime.now(UTC)
    if record is None or record.is_expired(now):
        raise HandleNotResolvable()
    return record.target_account_id
