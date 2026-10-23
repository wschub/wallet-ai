"""Caso de uso: listar los borradores propios y obtener uno concreto."""

from __future__ import annotations

from uuid import UUID

from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.transfers.draft import Draft
from wallet_ai.domain.transfers.errors import DraftNotFound


async def list_drafts(repo: DraftRepositoryPort, *, user_id: UUID) -> list[Draft]:
    return await repo.list_for_user(user_id)


async def get_draft(repo: DraftRepositoryPort, *, draft_id: UUID, user_id: UUID) -> Draft:
    draft = await repo.get(draft_id)
    if draft is None or draft.user_id != user_id:
        raise DraftNotFound("draft not found")
    return draft
