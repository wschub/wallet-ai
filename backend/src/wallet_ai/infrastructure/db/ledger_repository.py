from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal
from typing import Any
from uuid import UUID

import asyncpg

from wallet_ai.domain.ledger.errors import (
    AccountNotFound,
    AccountNotOwned,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    SystemAccountNotAllowed,
)


class LedgerRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    @staticmethod
    def _validate_amount(amount: Decimal | str | int | float) -> Decimal:
        if isinstance(amount, float):
            raise InvalidAmount("float not allowed; use Decimal/string")
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        # reject > 2 decimals
        q = d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if d != q:
            raise InvalidAmount("amount must have at most 2 decimal places")
        if d <= 0:
            raise InvalidAmount("amount must be > 0")
        if d > Decimal("999999999999999999.99"):
            raise InvalidAmount("amount overflows NUMERIC(18,2)")
        return d

    async def get_balance(self, account_id: UUID | str) -> Decimal:
        if isinstance(account_id, str):
            account_id = UUID(account_id)
        row = await self._conn.fetchrow(
            "SELECT COALESCE(SUM(CASE WHEN entry_type='CREDIT' THEN amount ELSE 0 END),0) "
            "- COALESCE(SUM(CASE WHEN entry_type='DEBIT' THEN amount ELSE 0 END),0) AS balance "
            "FROM ledger_entries WHERE account_id = $1",
            account_id,
        )
        return Decimal(str(row["balance"])) if row else Decimal("0")

    async def fund_account(
        self,
        *,
        to_account_id: UUID | str,
        amount: Decimal | str | int | float,
        idempotency_key: str,
        request_hash: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(to_account_id, str):
            to_account_id = UUID(to_account_id)
        d = self._validate_amount(amount)
        async with self._conn.transaction():
            sys = await self._conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
            if sys is None:
                raise AccountNotFound("system account not found")
            ids = sorted([str(sys["id"]), str(to_account_id)])
            await self._conn.execute(
                "SELECT id FROM accounts WHERE id = ANY($1) ORDER BY id FOR UPDATE",
                [UUID(i) for i in ids],
            )
            try:
                row = await self._conn.fetchrow(
                    "INSERT INTO ledger_transactions "
                    "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) "
                    "ON CONFLICT DO NOTHING RETURNING id, request_hash",
                    None,
                    None,
                    idempotency_key,
                    request_hash,
                )
            except asyncpg.exceptions.UniqueViolationError:
                row = None
            if row is None:
                existing = await self._conn.fetchrow(
                    "SELECT id, request_hash FROM ledger_transactions "
                    "WHERE from_user_id IS NULL AND from_account_id IS NULL AND idempotency_key = $1",
                    idempotency_key,
                )
                if existing is None:
                    raise IdempotencyConflict("idempotency conflict")
                entries = await self._conn.fetch(
                    "SELECT id, account_id, entry_type, amount FROM ledger_entries WHERE transaction_id = $1",
                    existing["id"],
                )
                return {
                    "transaction_id": str(existing["id"]),
                    "entries": [dict(e) for e in entries],
                    "idempotent": True,
                }
            txn_id = row["id"]
            debit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3) RETURNING id",
                txn_id,
                sys["id"],
                d,
            )
            credit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3) RETURNING id",
                txn_id,
                to_account_id,
                d,
            )
            await self._conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return {
                "transaction_id": str(txn_id),
                "entries": [
                    {"id": str(debit_id), "account_id": str(sys["id"]), "entry_type": "DEBIT", "amount": str(d)},
                    {"id": str(credit_id), "account_id": str(to_account_id), "entry_type": "CREDIT", "amount": str(d)},
                ],
                "idempotent": False,
            }

    async def transfer(
        self,
        *,
        from_user_id: UUID | str,
        from_account_id: UUID | str,
        to_account_id: UUID | str,
        amount: Decimal | str | int | float,
        idempotency_key: str,
        request_hash: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(from_user_id, str):
            from_user_id = UUID(from_user_id)
        if isinstance(from_account_id, str):
            from_account_id = UUID(from_account_id)
        if isinstance(to_account_id, str):
            to_account_id = UUID(to_account_id)

        if from_account_id == to_account_id:
            raise SameAccountTransfer("cannot transfer to same account")

        d = self._validate_amount(amount)

        async with self._conn.transaction():
            src = await self._conn.fetchrow("SELECT is_system FROM accounts WHERE id = $1", from_account_id)
            dst = await self._conn.fetchrow("SELECT is_system FROM accounts WHERE id = $1", to_account_id)
            if (src and src["is_system"]) or (dst and dst["is_system"]):
                raise SystemAccountNotAllowed("system account not allowed")
            # check ownership
            own = await self._conn.fetchrow(
                "SELECT id FROM accounts WHERE id = $1 AND user_id = $2",
                from_account_id,
                from_user_id,
            )
            if own is None:
                raise AccountNotOwned("source account not owned by user")

            # lock accounts in order
            ids = sorted([str(from_account_id), str(to_account_id)])
            await self._conn.execute(
                "SELECT id FROM accounts WHERE id = ANY($1) ORDER BY id FOR UPDATE",
                [UUID(i) for i in ids],
            )

            bal = await self.get_balance(from_account_id)
            if bal < d:
                raise InsufficientFunds("insufficient funds")

            try:
                row = await self._conn.fetchrow(
                    "INSERT INTO ledger_transactions "
                    "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, 'transfer', $4) "
                    "ON CONFLICT DO NOTHING RETURNING id, request_hash",
                    from_user_id,
                    from_account_id,
                    idempotency_key,
                    request_hash,
                )
            except asyncpg.exceptions.UniqueViolationError:
                row = None

            if row is None:
                existing = await self._conn.fetchrow(
                    "SELECT id, request_hash FROM ledger_transactions "
                    "WHERE from_user_id = $1 AND from_account_id = $2 AND idempotency_key = $3",
                    from_user_id,
                    from_account_id,
                    idempotency_key,
                )
                if existing is None:
                    raise IdempotencyConflict("idempotency conflict")
                if (existing["request_hash"] or "") != (request_hash or ""):
                    raise IdempotencyConflict("idempotency conflict")
                entries = await self._conn.fetch(
                    "SELECT id, account_id, entry_type, amount FROM ledger_entries WHERE transaction_id = $1",
                    existing["id"],
                )
                return {
                    "transaction_id": str(existing["id"]),
                    "entries": [dict(e) for e in entries],
                    "idempotent": True,
                }

            txn_id = row["id"]
            if (row["request_hash"] or "") != (request_hash or ""):
                raise IdempotencyConflict("idempotency conflict")

            debit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3) RETURNING id",
                txn_id,
                from_account_id,
                d,
            )
            credit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3) RETURNING id",
                txn_id,
                to_account_id,
                d,
            )
            await self._conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return {
                "transaction_id": str(txn_id),
                "entries": [
                    {"id": str(debit_id), "account_id": str(from_account_id), "entry_type": "DEBIT", "amount": str(d)},
                    {"id": str(credit_id), "account_id": str(to_account_id), "entry_type": "CREDIT", "amount": str(d)},
                ],
                "idempotent": False,
            }
        if isinstance(from_user_id, str):
            from_user_id = UUID(from_user_id)
        if isinstance(from_account_id, str):
            from_account_id = UUID(from_account_id)
        if isinstance(to_account_id, str):
            to_account_id = UUID(to_account_id)

        if from_account_id == to_account_id:
            raise SameAccountTransfer("cannot transfer to same account")

        d = self._validate_amount(amount)

        async with self._conn.transaction():
            # check ownership
            own = await self._conn.fetchrow(
                "SELECT id FROM accounts WHERE id = $1 AND user_id = $2",
                from_account_id,
                from_user_id,
            )
            if own is None:
                raise AccountNotOwned("source account not owned by user")

            if not await self._conn.fetchrow("SELECT id FROM accounts WHERE id = $1", to_account_id):
                raise AccountNotFound("destination account not found")

            # lock accounts in order
            ids = sorted([str(from_account_id), str(to_account_id)])
            await self._conn.execute(
                "SELECT id FROM accounts WHERE id = ANY($1) ORDER BY id FOR UPDATE",
                [UUID(i) for i in ids],
            )

            bal = await self.get_balance(from_account_id)
            if bal < d:
                raise InsufficientFunds("insufficient funds")

            txn_id = UUID(int=0)  # placeholder
            # try idempotent insert
            try:
                row = await self._conn.fetchrow(
                    "INSERT INTO ledger_transactions "
                    "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, 'transfer', $4) "
                    "ON CONFLICT DO NOTHING RETURNING id, request_hash",
                    from_user_id,
                    from_account_id,
                    idempotency_key,
                    request_hash,
                )
            except asyncpg.exceptions.UniqueViolationError:
                row = None

            if row is None:
                # fetch existing
                existing = await self._conn.fetchrow(
                    "SELECT id, request_hash FROM ledger_transactions "
                    "WHERE from_user_id = $1 AND from_account_id = $2 AND idempotency_key = $3",
                    from_user_id,
                    from_account_id,
                    idempotency_key,
                )
                if existing is None:
                    raise IdempotencyConflict("idempotency conflict")
                if (existing["request_hash"] or "") != (request_hash or ""):
                    raise IdempotencyConflict("idempotency conflict")
                # verify same amount/dest? we store amount in entries; return existing
                entries = await self._conn.fetch(
                    "SELECT id, account_id, entry_type, amount FROM ledger_entries WHERE transaction_id = $1",
                    existing["id"],
                )
                return {
                    "transaction_id": str(existing["id"]),
                    "entries": [dict(e) for e in entries],
                    "idempotent": True,
                }

            txn_id = row["id"]
            if (row["request_hash"] or "") != (request_hash or ""):
                raise IdempotencyConflict("idempotency conflict")

            # insert entries
            debit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3) RETURNING id",
                txn_id,
                from_account_id,
                d,
            )
            credit_id = await self._conn.fetchval(
                "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3) RETURNING id",
                txn_id,
                to_account_id,
                d,
            )
            await self._conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return {
                "transaction_id": str(txn_id),
                "entries": [
                    {"id": str(debit_id), "account_id": str(from_account_id), "entry_type": "DEBIT", "amount": str(d)},
                    {"id": str(credit_id), "account_id": str(to_account_id), "entry_type": "CREDIT", "amount": str(d)},
                ],
                "idempotent": False,
            }

    async def list_accounts_with_balance(self, user_id: UUID | str):
        if isinstance(user_id, str):
            user_id = UUID(user_id)
        rows = await self._conn.fetch(
            "SELECT a.id, a.currency, "
            "COALESCE(SUM(CASE WHEN e.entry_type='CREDIT' THEN e.amount ELSE 0 END),0) "
            "- COALESCE(SUM(CASE WHEN e.entry_type='DEBIT' THEN e.amount ELSE 0 END),0) AS balance "
            "FROM accounts a LEFT JOIN ledger_entries e ON e.account_id = a.id "
            "WHERE a.user_id = $1 GROUP BY a.id, a.currency ORDER BY a.id",
            user_id,
        )
        return rows

    async def list_transactions(self, account_id: UUID | str, limit: int = 20, cursor: str | None = None):
        if isinstance(account_id, str):
            account_id = UUID(account_id)
        limit = max(1, min(limit, 100))
        if cursor:
            try:
                cur = json.loads(cursor)
                cur_ts = cur["ts"]
                cur_id = UUID(cur["id"])
            except Exception:
                cur_ts = None
                cur_id = None
        else:
            cur_ts = None
            cur_id = None

        if cur_ts and cur_id:
            rows = await self._conn.fetch(
                "SELECT e.id, e.entry_type, e.amount, e.created_at, e.transaction_id "
                "FROM ledger_entries e WHERE e.account_id = $1 AND (e.created_at, e.id) < ($2, $3) "
                "ORDER BY e.created_at DESC, e.id DESC LIMIT $4",
                account_id,
                cur_ts,
                cur_id,
                limit + 1,
            )
        else:
            rows = await self._conn.fetch(
                "SELECT e.id, e.entry_type, e.amount, e.created_at, e.transaction_id "
                "FROM ledger_entries e WHERE e.account_id = $1 "
                "ORDER BY e.created_at DESC, e.id DESC LIMIT $2",
                account_id,
                limit + 1,
            )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = json.dumps({"ts": last["created_at"].isoformat(), "id": str(last["id"])})
        return items, next_cursor
