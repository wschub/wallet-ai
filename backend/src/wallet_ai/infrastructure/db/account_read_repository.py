"""Repositorio SELECT-only de cuentas y movimientos.

Implementa `wallet_ai.domain.ports.account_reader.AccountReaderPort` sobre
una conexion asyncpg existente. Solo lee: no abre transacciones ni escribe.
El saldo se deriva de los asientos (CREDITO - DEBITO) y el listado pagina de
forma estable por (created_at, id) con el mismo cursor opaco que usa
`GET /accounts/me/transactions`.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import asyncpg

from wallet_ai.domain.accounts.errors import AccountNotFound
from wallet_ai.domain.ports.account_reader import AccountBalance, Movement


class AccountReadRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def primary_account_id(self, user_id: UUID) -> UUID | None:
        return await self._conn.fetchval(
            "SELECT id FROM accounts "
            "WHERE user_id = $1 AND is_system = false "
            "ORDER BY created_at, id LIMIT 1",
            user_id,
        )

    async def balance(self, *, account_id: UUID) -> AccountBalance:
        row = await self._conn.fetchrow(
            "SELECT a.currency, "
            "COALESCE(SUM(CASE WHEN e.entry_type='CREDIT' THEN e.amount ELSE 0 END),0) "
            "- COALESCE(SUM(CASE WHEN e.entry_type='DEBIT' THEN e.amount ELSE 0 END),0) "
            "AS balance "
            "FROM accounts a LEFT JOIN ledger_entries e ON e.account_id = a.id "
            "WHERE a.id = $1 GROUP BY a.id, a.currency",
            account_id,
        )
        if row is None:
            raise AccountNotFound("account not found")
        return AccountBalance(balance=row["balance"], currency=row["currency"])

    async def movements(
        self, *, account_id: UUID, limit: int, cursor: str | None
    ) -> tuple[list[Movement], str | None]:
        cur_ts, cur_id = _parse_cursor(cursor)
        if cur_ts is not None and cur_id is not None:
            rows = await self._conn.fetch(
                "SELECT id, entry_type, amount, created_at FROM ledger_entries "
                "WHERE account_id = $1 AND (created_at, id) < ($2, $3) "
                "ORDER BY created_at DESC, id DESC LIMIT $4",
                account_id,
                cur_ts,
                cur_id,
                limit + 1,
            )
        else:
            rows = await self._conn.fetch(
                "SELECT id, entry_type, amount, created_at FROM ledger_entries "
                "WHERE account_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
                account_id,
                limit + 1,
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = json.dumps(
                {"ts": last["created_at"].isoformat(), "id": str(last["id"])}
            )
        return [
            Movement(
                id=r["id"],
                entry_type=r["entry_type"],
                amount=r["amount"],
                created_at=r["created_at"],
            )
            for r in page
        ], next_cursor


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    """Cursor opaco {"ts": iso, "id": uuid}; si viene corrupto se ignora
    (mismo comportamiento que el endpoint `/accounts/me/transactions`)."""
    if not cursor:
        return None, None
    try:
        raw = json.loads(cursor)
        ts = datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00"))
        return ts, UUID(str(raw["id"]))
    except Exception:
        return None, None
