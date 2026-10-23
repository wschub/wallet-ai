"""Repositorio asyncpg de borradores de transferencia.

Implementa `wallet_ai.domain.ports.drafts.DraftRepositoryPort` sobre una
conexion ya establecida. No abre transacciones por si mismo: el caso de uso
`confirm_draft` es quien abre la transaccion para que la actualizacion del
borrador y el movimiento de dinero en el ledger sean atomicos.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from wallet_ai.domain.transfers.draft import Draft

_COLUMNS = (
    "id, user_id, from_account_id, to_account_id, amount, status, expires_at, "
    "confirmed_at, ledger_transaction_id, trace_id, created_at"
)


def _row_to_draft(row: asyncpg.Record) -> Draft:
    return Draft(
        id=row["id"],
        user_id=row["user_id"],
        from_account_id=row["from_account_id"],
        to_account_id=row["to_account_id"],
        amount=Decimal(str(row["amount"])),
        status=row["status"],
        expires_at=row["expires_at"],
        confirmed_at=row["confirmed_at"],
        ledger_transaction_id=row["ledger_transaction_id"],
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


class DraftRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def create(
        self,
        *,
        user_id: UUID,
        from_account_id: UUID,
        to_account_id: UUID,
        amount: Decimal,
        expires_at: datetime,
        trace_id: str | None = None,
    ) -> Draft:
        row = await self._conn.fetchrow(
            "INSERT INTO transfer_drafts "
            "(id, user_id, from_account_id, to_account_id, amount, status, expires_at, trace_id) "
            f"VALUES (gen_random_uuid(), $1, $2, $3, $4, 'pendiente', $5, $6) RETURNING {_COLUMNS}",
            user_id,
            from_account_id,
            to_account_id,
            amount,
            expires_at,
            trace_id,
        )
        return _row_to_draft(row)

    async def get(self, draft_id: UUID, *, for_update: bool = False) -> Draft | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await self._conn.fetchrow(
            f"SELECT {_COLUMNS} FROM transfer_drafts WHERE id = $1{suffix}",
            draft_id,
        )
        return _row_to_draft(row) if row else None

    async def list_for_user(self, user_id: UUID) -> list[Draft]:
        rows = await self._conn.fetch(
            f"SELECT {_COLUMNS} FROM transfer_drafts WHERE user_id = $1 "
            "ORDER BY created_at DESC, id DESC",
            user_id,
        )
        return [_row_to_draft(r) for r in rows]

    async def mark_expired(self, draft_id: UUID) -> None:
        await self._conn.execute(
            "UPDATE transfer_drafts SET status = 'expirada' WHERE id = $1 AND status = 'pendiente'",
            draft_id,
        )

    async def mark_rejected(self, draft_id: UUID) -> None:
        await self._conn.execute(
            "UPDATE transfer_drafts SET status = 'rechazada' WHERE id = $1 AND status = 'pendiente'",
            draft_id,
        )

    async def mark_confirmed(
        self,
        draft_id: UUID,
        *,
        ledger_transaction_id: UUID,
        confirmed_at: datetime,
    ) -> None:
        await self._conn.execute(
            "UPDATE transfer_drafts "
            "SET status = 'confirmada', ledger_transaction_id = $2, confirmed_at = $3 "
            "WHERE id = $1 AND status = 'pendiente'",
            draft_id,
            ledger_transaction_id,
            confirmed_at,
        )
