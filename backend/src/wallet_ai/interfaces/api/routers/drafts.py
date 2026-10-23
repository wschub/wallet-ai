"""Endpoints de borradores de transferencia.

- GET  /drafts                 -> borradores del usuario (solo rol cliente)
- GET  /drafts/{id}            -> borrador concreto (404 si no es propio)
- POST /drafts/{id}/reject     -> rechaza un borrador pendiente
- POST /drafts/{id}/confirm    -> confirma (unico punto donde se mueve dinero)

Todos devuelven el header `X-Trace-Id` (lo añade el middleware) y los montos
se serializan como string decimal.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from wallet_ai.application.ledger.service import LedgerService
from wallet_ai.application.transfers.confirm_draft import confirm_draft
from wallet_ai.application.transfers.list_drafts import get_draft, list_drafts
from wallet_ai.application.transfers.reject_draft import reject_draft
from wallet_ai.domain.transfers.draft import Draft
from wallet_ai.domain.transfers.errors import (
    DraftExpired,
    DraftNotConfirmable,
    DraftNotFound,
)
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.draft_repository import DraftRepository
from wallet_ai.infrastructure.db.ledger_repository import LedgerRepository
from wallet_ai.interfaces.api.deps.roles import require_role

router = APIRouter(prefix="/drafts", tags=["drafts"])


async def _get_conn():
    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


def _amount_str(amount: Decimal) -> str:
    return f"{Decimal(str(amount)).quantize(Decimal('0.01')):.2f}"


def _serialize(draft: Draft) -> dict:
    return {
        "id": str(draft.id),
        "status": draft.status,
        "amount": _amount_str(draft.amount),
        "from_account_id": str(draft.from_account_id),
        "to_account_id": str(draft.to_account_id),
        "expires_at": draft.expires_at.isoformat(),
        "confirmed_at": draft.confirmed_at.isoformat() if draft.confirmed_at else None,
        "ledger_transaction_id": (
            str(draft.ledger_transaction_id) if draft.ledger_transaction_id else None
        ),
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }


def _user_uuid(user: dict) -> UUID:
    raw = user.get("sub") or user.get("id")
    try:
        return UUID(str(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="bad user")


@router.get("")
async def list_my_drafts(
    request: Request,
    user=Depends(require_role("cliente")),
    conn=Depends(_get_conn),
):
    drafts = await list_drafts(DraftRepository(conn), user_id=_user_uuid(user))
    return [_serialize(d) for d in drafts]


@router.get("/{draft_id}")
async def get_my_draft(
    draft_id: UUID,
    request: Request,
    user=Depends(require_role("cliente")),
    conn=Depends(_get_conn),
):
    try:
        draft = await get_draft(DraftRepository(conn), draft_id=draft_id, user_id=_user_uuid(user))
    except DraftNotFound:
        raise HTTPException(status_code=404, detail="draft not found")
    return _serialize(draft)


@router.post("/{draft_id}/reject")
async def reject_my_draft(
    draft_id: UUID,
    request: Request,
    user=Depends(require_role("cliente")),
    conn=Depends(_get_conn),
):
    try:
        draft = await reject_draft(DraftRepository(conn), draft_id=draft_id, user_id=_user_uuid(user))
    except DraftNotFound:
        raise HTTPException(status_code=404, detail="draft not found")
    except DraftNotConfirmable:
        raise HTTPException(status_code=409, detail="draft not pending")
    return _serialize(draft)


@router.post("/{draft_id}/confirm")
async def confirm_my_draft(
    draft_id: UUID,
    request: Request,
    user=Depends(require_role("cliente")),
    conn=Depends(_get_conn),
):
    try:
        result = await confirm_draft(
            drafts=DraftRepository(conn),
            ledger=LedgerService(LedgerRepository(conn)),
            transaction=conn.transaction,
            draft_id=draft_id,
            user_id=_user_uuid(user),
        )
    except DraftNotFound:
        raise HTTPException(status_code=404, detail="draft not found")
    except (DraftNotConfirmable, DraftExpired):
        raise HTTPException(status_code=409, detail="draft not confirmable")
    return result
