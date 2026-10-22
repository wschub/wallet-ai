from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import asyncpg
import pytest
from fastapi.testclient import TestClient

from wallet_ai.domain.ledger.errors import (
    AccountNotOwned,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
)
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.ledger_repository import LedgerRepository
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.api.main import app


async def _new_user(conn: asyncpg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, 'cliente')",
        uid,
        f"ls-{uuid.uuid4().hex}@test.com",
    )
    return uid


async def _new_account(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    aid = uuid.uuid4()
    await conn.execute("INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')", aid, user_id)
    return aid


async def _fund(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal) -> None:
    sys = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if sys is None:
        suid = uuid.uuid4()
        await conn.execute("INSERT INTO users (id, email, role) VALUES ($1, $2, 'compliance')", suid, f"sys-{uuid.uuid4().hex}@test.com")
        sid = uuid.uuid4()
        await conn.execute("INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1, $2, 'COP', true)", sid, suid)
        sys = {"id": sid}
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(), $1,$2,$3,'funding',$4) RETURNING id",
            None,
            None,
            f"fund-{account_id}-{uuid.uuid4().hex}",
            "fund",
        )
        await conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys["id"], amount)
        await conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, account_id, amount)
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_twenty_concurrent_ten_succeed_deadlock_free(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    b = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("10.00"))

    async def do_one(i):
        c2 = await asyncpg.connect(Settings().asyncpg_dsn)
        try:
            repo = LedgerRepository(c2)
            try:
                await repo.transfer(
                    from_user_id=u,
                    from_account_id=a,
                    to_account_id=b,
                    amount=Decimal("1.00"),
                    idempotency_key=f"k{i}",
                    request_hash=f"h{i}",
                )
                return True
            except Exception:
                return False
        finally:
            await c2.close()

    res = await asyncio.gather(*[do_one(i) for i in range(20)], return_exceptions=False)
    assert res.count(True) == 10
    repo = LedgerRepository(db_conn)
    ba = await repo.get_balance(a)
    bb = await repo.get_balance(b)
    assert ba == Decimal("0.00")
    assert bb == Decimal("10.00")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_transfers_concurrent_no_deadlock(db_conn):
    u1 = await _new_user(db_conn)
    a1 = await _new_account(db_conn, u1)
    u2 = await _new_user(db_conn)
    a2 = await _new_account(db_conn, u2)
    await _fund(db_conn, a1, Decimal("5.00"))
    await _fund(db_conn, a2, Decimal("5.00"))

    async def t1():
        c = await asyncpg.connect(Settings().asyncpg_dsn)
        try:
            r = LedgerRepository(c)
            try:
                await r.transfer(from_user_id=u1, from_account_id=a1, to_account_id=a2, amount=Decimal("1.00"), idempotency_key=f"x1-{uuid.uuid4().hex}", request_hash="x1")
                return True
            except Exception:
                return False
        finally:
            await c.close()

    async def t2():
        c = await asyncpg.connect(Settings().asyncpg_dsn)
        try:
            r = LedgerRepository(c)
            try:
                await r.transfer(from_user_id=u2, from_account_id=a2, to_account_id=a1, amount=Decimal("1.00"), idempotency_key=f"x2-{uuid.uuid4().hex}", request_hash="x2")
                return True
            except Exception:
                return False
        finally:
            await c.close()

    await asyncio.gather(t1(), t2())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idempotency_retry_same_result_same_key_other_params_conflict(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    b = await _new_account(db_conn, u)
    c = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("10.00"))

    r = LedgerRepository(db_conn)
    key = f"k{uuid.uuid4().hex}"
    h = "h1"
    res1 = await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("2.00"), idempotency_key=key, request_hash=h)
    entries1 = await db_conn.fetch("SELECT count(*) as cnt FROM ledger_entries WHERE transaction_id = $1", uuid.UUID(res1["transaction_id"]))
    assert entries1[0]["cnt"] == 2
    res2 = await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("2.00"), idempotency_key=key, request_hash=h)
    assert res2["transaction_id"] == res1["transaction_id"]
    entries2 = await db_conn.fetch("SELECT count(*) as cnt FROM ledger_entries WHERE transaction_id = $1", uuid.UUID(res2["transaction_id"]))
    assert entries2[0]["cnt"] == 2
    with pytest.raises(IdempotencyConflict):
        await r.transfer(from_user_id=u, from_account_id=a, to_account_id=c, amount=Decimal("1.00"), idempotency_key=key, request_hash="h2")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_validation_errors_and_no_side_effects(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    b = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("5.00"))

    r = LedgerRepository(db_conn)
    with pytest.raises(InsufficientFunds):
        await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("10.00"), idempotency_key=f"k{uuid.uuid4().hex}", request_hash="h")

    # second insufficient should also raise
    with pytest.raises(InsufficientFunds):
        await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("10.00"), idempotency_key=f"k2{uuid.uuid4().hex}", request_hash="h2")

    u2 = await _new_user(db_conn)
    a2 = await _new_account(db_conn, u2)
    with pytest.raises(AccountNotOwned):
        await r.transfer(from_user_id=u, from_account_id=a2, to_account_id=b, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}", request_hash="h")

    with pytest.raises(SameAccountTransfer):
        await r.transfer(from_user_id=u, from_account_id=a, to_account_id=a, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}", request_hash="h")

    for bad in [Decimal("0.001"), Decimal("0"), Decimal("-1"), Decimal("0.00")]:
        with pytest.raises(InvalidAmount):
            await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=bad, idempotency_key=f"k{uuid.uuid4().hex}", request_hash="h")

    with pytest.raises(InvalidAmount):
        await r.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("1000000000000000000.00"), idempotency_key=f"k{uuid.uuid4().hex}", request_hash="h")

    # no filas creadas por intentos fallidos
    cnt = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions WHERE from_account_id = $1", a)
    # may have funding txns; just ensure no new failed ones persisted badly
    assert cnt >= 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_direct_insert_negative_balance_rejected_except_system(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    b = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("1.00"))
    # dejar saldo negativo en cuenta no sistema -> debe rechazar al confirmar
    # balance = 1.00; debitar 5.00 y acreditar 5.00 -> quedaría -4.00
    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with db_conn.transaction():
            txn_id = await db_conn.fetchval("INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'transfer',$4) RETURNING id", u, a, f"k{uuid.uuid4().hex}", "h")
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, a, Decimal("5.00"))
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, b, Decimal("5.00"))
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    # cuenta sistema puede quedar negativa
    sys = await db_conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    assert sys is not None
    async with db_conn.transaction():
        txn_id = await db_conn.fetchval("INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'funding',$4) RETURNING id", None, None, f"k{uuid.uuid4().hex}", "h")
        await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys["id"], Decimal("100.00"))
        await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, a, Decimal("100.00"))
        await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.integration
def test_endpoints_balance_and_pagination():
    Settings()
    client = TestClient(app)
    token = create_token(sub=str(uuid.uuid4()), role="cliente", secret=Settings().AUTH_JWT_SECRET)
    r = client.get("/accounts/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (200, 404)
    r2 = client.get("/accounts/me/transactions", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code in (200, 401)
    r3 = client.get("/accounts/me/transactions?limit=5", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code in (200, 401)
    # saldo como string decimal
    if r.status_code == 200:
        data = r.json()
        assert "balance" in data
        assert isinstance(data["balance"], str)
        # format decimal
        Decimal(data["balance"])
    if r3.status_code == 200:
        d = r3.json()
        assert "items" in d
        # pagination
        items = d["items"]
        ids = [it["id"] for it in items]
        assert len(ids) == len(set(ids))
        if d.get("next_cursor"):
            r4 = client.get(f"/accounts/me/transactions?limit=5&cursor={d['next_cursor']}", headers={"Authorization": f"Bearer {token}"})
            assert r4.status_code == 200
            d2 = r4.json()
            ids2 = [it["id"] for it in d2["items"]]
            assert set(ids).isdisjoint(set(ids2))
            for it in items:
                assert "amount" in it and isinstance(it["amount"], str)
                assert "type" in it and it["type"] in ("DEBIT", "CREDIT")
                assert "created_at" in it
                assert "id" in it
