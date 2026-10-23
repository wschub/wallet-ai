"""Caso de uso: rechazar un borrador de transferencia."""

from __future__ import annotations

from uuid import UUID

from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.transfers.draft import PENDIENTE, Draft
from wallet_ai.domain.transfers.errors import DraftNotConfirmable, DraftNotFound


async def reject_draft(repo: DraftRepositoryPort, *, draft_id: UUID, user_id: UUID) -> Draft:
    draft = await repo.get(draft_id, for_update=True)
    if draft is None or draft.user_id != user_id:
        raise DraftNotFound("draft not found")
    if draft.status != PENDIENTE:
        raise DraftNotConfirmable(f"draft is {draft.status}")

    await repo.mark_rejected(draft_id)
    updated = await repo.get(draft_id)
    assert updated is not None
    return updated
