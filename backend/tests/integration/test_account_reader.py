"""Tramo B2 (T042): lectura de cuentas sin pasar por el ledger.

`application/accounts` ofrece un servicio de solo lectura: saldo derivado de
los asientos y listado de movimientos con `limit` y cursor estable por
`(created_at, id)`, sobre el puerto `domain/ports/account_reader.py` con
implementacion SELECT-only en `infrastructure/db/account_read_repository.py`.
No importa `application.ledger` ni `ledger_repository` (lo impone el contrato
import-linter "Confirmation isolation").
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import pytest

from wallet_ai.application.accounts.service import get_balance, list_transactions
from wallet_ai.domain.accounts.errors import AccountNotFound
from wallet_ai.infrastructure.db.account_read_repository import AccountReadRepository

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _new_user(conn: asyncpg.Connection, role: str = "cliente") -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, $3)",
        uid,
        f"accts-{uuid.uuid4().hex}@test.com",
        role,
    )
    return uid


async def _new_account(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    aid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')",
        aid,
        user_id,
    )
    return aid


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    suid = await _new_user(conn)
    sid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1, $2, 'COP', true)",
        sid,
        suid,
    )
    return sid


async def _credit(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal) -> None:
    """Un fondeo: DEBITO en la cuenta del sistema + CREDITO en la cuenta dada."""
    sys = await _ensure_system_account(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions "
            "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) RETURNING id",
            None,
            None,
            f"accts-{uuid.uuid4().hex}",
            "fund",
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3)",
            txn_id,
            sys,
            amount,
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3)",
            txn_id,
            account_id,
            amount,
        )
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --------------------------------------------------------------------------
# saldo derivado de asientos
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_balance_is_derived_from_ledger_entries(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    reader = AccountReadRepository(db_conn)

    # sin movimientos: 0.00
    result = await get_balance(reader, user_id=user)
    assert result == {"balance": "0.00", "currency": "COP"}

    await _credit(db_conn, account, Decimal("100.00"))
    await _credit(db_conn, account, Decimal("25.50"))

    result = await get_balance(reader, user_id=user)
    assert result == {"balance": "125.50", "currency": "COP"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_balance_without_account_fails(db_conn):
    user = await _new_user(db_conn)
    reader = AccountReadRepository(db_conn)

    with pytest.raises(AccountNotFound):
        await get_balance(reader, user_id=user)


# --------------------------------------------------------------------------
# movimientos: limit y cursor estable por (created_at, id)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_transactions_limit_and_stable_cursor(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    reader = AccountReadRepository(db_conn)
    for amount in ("10.00", "20.00", "30.00", "40.00", "50.00"):
        await _credit(db_conn, account, Decimal(amount))

    page1 = await list_transactions(reader, user_id=user, limit=2)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    for item in page1["items"]:
        assert isinstance(item["amount"], str)
        assert item["type"] == "CREDIT"
        assert "created_at" in item and "id" in item

    page2 = await list_transactions(reader, user_id=user, limit=2, cursor=page1["next_cursor"])
    page3 = await list_transactions(reader, user_id=user, limit=2, cursor=page2["next_cursor"])

    ids1 = {it["id"] for it in page1["items"]}
    ids2 = {it["id"] for it in page2["items"]}
    ids3 = {it["id"] for it in page3["items"]}
    assert len(ids2) == 2
    assert len(ids3) == 1
    assert ids1.isdisjoint(ids2) and ids1.isdisjoint(ids3) and ids2.isdisjoint(ids3)
    assert page3["next_cursor"] is None

    # cursor estable: repetir la primera pagina da el mismo resultado
    again = await list_transactions(reader, user_id=user, limit=2)
    assert {it["id"] for it in again["items"]} == ids1

    # cada pagina trae sus montos como string decimal
    for it in page2["items"] + page3["items"]:
        assert isinstance(it["amount"], str)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transactions_require_account(db_conn):
    user = await _new_user(db_conn)
    reader = AccountReadRepository(db_conn)

    with pytest.raises(AccountNotFound):
        await list_transactions(reader, user_id=user, limit=20)
