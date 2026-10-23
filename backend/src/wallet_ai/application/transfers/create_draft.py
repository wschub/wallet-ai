"""Caso de uso: crear un borrador de transferencia.

Este tramo (US2-A) no expone `POST /drafts`: los borradores se crean por
servicio en los tests. La validacion de handles (`recipient_id`) llega en el
tramo de handles; aqui se trabaja ya con cuentas resueltas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.transfers.draft import Draft
from wallet_ai.domain.transfers.errors import InvalidDraft

DEFAULT_TTL_MINUTES = 15


async def create_draft(
    repo: DraftRepositoryPort,
    *,
    user_id: UUID,
    from_account_id: UUID,
    to_account_id: UUID,
    amount: Decimal,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    trace_id: str | None = None,
    now: datetime | None = None,
) -> Draft:
    if from_account_id == to_account_id:
        raise InvalidDraft("from_account_id and to_account_id must differ")
    if amount <= 0:
        raise InvalidDraft("amount must be > 0")

    moment = now or datetime.now(UTC)
    expires_at = moment + timedelta(minutes=ttl_minutes)
    return await repo.create(
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=amount,
        expires_at=expires_at,
        trace_id=trace_id,
    )
