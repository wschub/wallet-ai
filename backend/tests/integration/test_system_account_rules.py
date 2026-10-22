from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import pytest
from fastapi.testclient import TestClient

from wallet_ai.domain.ledger.errors import SystemAccountNotAllowed
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.ledger_repository import LedgerRepository
from wallet_ai.interfaces.api.deps.auth import verify_password
from wallet_ai.interfaces.api.main import app


async def _new_user(conn: asyncpg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute("INSERT INTO users (id, email, role) VALUES ($1,$2,'cliente')", uid, f"ts-{uuid.uuid4().hex}@test.com")
    return uid


async def _new_account(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    aid = uuid.uuid4()
    await conn.execute("INSERT INTO accounts (id, user_id, currency) VALUES ($1,$2,'COP')", aid, user_id)
    return aid


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    suid = uuid.uuid4()
    await conn.execute("INSERT INTO users (id, email, role) VALUES ($1,$2,'cliente') ON CONFLICT DO NOTHING", suid, f"sys-{uuid.uuid4().hex}@test.com")
    sid = uuid.uuid4()
    await conn.execute("INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1,$2,'COP',true)", sid, suid)
    return sid


async def _fund(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal) -> None:
    sys = await _ensure_system_account(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'funding',$4) RETURNING id",
            None,
            None,
            f"fund-{account_id}-{uuid.uuid4().hex}",
            "fund",
        )
        await conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys, amount)
        await conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, account_id, amount)
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_transfer_rejects_system_origin_and_destination_and_no_rows(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("10.00"))
    sys = await _ensure_system_account(db_conn)
    repo = LedgerRepository(db_conn)
    cnt_txn_before = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions")
    cnt_ent_before = await db_conn.fetchval("SELECT count(*) FROM ledger_entries")
    from wallet_ai.application.ledger.service import LedgerService

    svc = LedgerService(repo)
    with pytest.raises(SystemAccountNotAllowed):
        await svc.transfer(from_user_id=u, from_account_id=sys, to_account_id=a, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}")
    with pytest.raises(SystemAccountNotAllowed):
        await svc.transfer(from_user_id=u, from_account_id=a, to_account_id=sys, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}")
    cnt_txn_after = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions")
    cnt_ent_after = await db_conn.fetchval("SELECT count(*) FROM ledger_entries")
    assert cnt_txn_after == cnt_txn_before
    assert cnt_ent_after == cnt_ent_before


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("pw_hash", ["INVALID-SYSTEM", None, "abc", "x:y"])
async def test_system_user_login_returns_401_with_various_hashes(db_conn, pw_hash):
    suid = uuid.uuid4()
    await db_conn.execute(
        "INSERT INTO users (id, email, role, password_hash) VALUES ($1,$2,'cliente',$3) ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash",
        suid,
        "system@wallet.ai",
        pw_hash,
    )
    Settings()
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": "system@wallet.ai", "password": "anything"})
    assert r.status_code == 401
    assert "X-Trace-Id" in r.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_system_user_not_compliance_role(db_conn):
    suid = uuid.uuid4()
    await db_conn.execute(
        "INSERT INTO users (id, email, role, password_hash) VALUES ($1,$2,'cliente','INVALID-SYSTEM') ON CONFLICT (email) DO UPDATE SET role='cliente', password_hash='INVALID-SYSTEM'",
        suid,
        "system@wallet.ai",
    )
    row = await db_conn.fetchrow("SELECT role FROM users WHERE email = 'system@wallet.ai'")
    assert row["role"] != "compliance"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repo_exposes_funding_separately_service_not_used(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    repo = LedgerRepository(db_conn)
    await _ensure_system_account(db_conn)
    res = await repo.fund_account(to_account_id=a, amount=Decimal("5.00"), idempotency_key=f"f{uuid.uuid4().hex}")
    assert res["transaction_id"]
    from wallet_ai.application.ledger.service import LedgerService

    svc = LedgerService(repo)
    try:
        await svc.transfer(from_user_id=u, from_account_id=a, to_account_id=a, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}", kind="funding")
        raise AssertionError("should not accept kind")
    except TypeError:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_trigger_rejects_transfer_to_or_from_system(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("10.00"))
    sys = await _ensure_system_account(db_conn)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with db_conn.transaction():
            txn_id = await db_conn.fetchval("INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'transfer',$4) RETURNING id", u, a, f"k{uuid.uuid4().hex}", "h")
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, a, Decimal("1.00"))
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, sys, Decimal("1.00"))
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with db_conn.transaction():
            txn_id = await db_conn.fetchval("INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'transfer',$4) RETURNING id", u, sys, f"k{uuid.uuid4().hex}", "h")
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys, Decimal("1.00"))
            await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, a, Decimal("1.00"))
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    async with db_conn.transaction():
        txn_id = await db_conn.fetchval("INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'funding',$4) RETURNING id", None, None, f"k{uuid.uuid4().hex}", "h")
        await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys, Decimal("1.00"))
        await db_conn.execute("INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, a, Decimal("1.00"))
        await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_verify_password_unusable_hashes():
    assert verify_password("x", "not-a-valid-hash") is False
    assert verify_password("x", "$2b$12$invalid") is False
    assert verify_password("x", "") is False
    assert verify_password("anything", "INVALID-SYSTEM") is False
