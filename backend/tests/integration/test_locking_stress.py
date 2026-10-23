from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import asyncpg
import pytest

from wallet_ai.application.ledger.service import LedgerService
from wallet_ai.domain.ledger.errors import InsufficientFunds
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.ledger_repository import LedgerRepository


async def _new_user(conn):
    uid = uuid.uuid4()
    await conn.execute("INSERT INTO users (id,email,role) VALUES ($1,$2,'cliente')", uid, f"ls-{uuid.uuid4().hex}@t.com")
    return uid


async def _new_account(conn, uid):
    aid = uuid.uuid4()
    await conn.execute("INSERT INTO accounts (id,user_id,currency) VALUES ($1,$2,'COP')", aid, uid)
    return aid


async def _ensure_system(conn):
    r = await conn.fetchrow("SELECT id FROM accounts WHERE is_system=true LIMIT 1")
    if r:
        return r["id"]
    suid = uuid.uuid4()
    await conn.execute("INSERT INTO users (id,email,role) VALUES ($1,$2,'compliance') ON CONFLICT DO NOTHING", suid, f"ss-{uuid.uuid4().hex}@t.com")
    sid = uuid.uuid4()
    await conn.execute("INSERT INTO accounts (id,user_id,currency,is_system) VALUES ($1,$2,'COP',true)", sid, suid)
    return sid


async def _fund(conn, aid, amt):
    sys = await _ensure_system(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval("INSERT INTO ledger_transactions (id,from_user_id,from_account_id,idempotency_key,kind,request_hash) VALUES (gen_random_uuid(),$1,$2,$3,'funding',$4) RETURNING id", None, None, f"ff-{aid}-{uuid.uuid4().hex}", "f")
        await conn.execute("INSERT INTO ledger_entries (id,transaction_id,account_id,entry_type,amount) VALUES (gen_random_uuid(),$1,$2,'DEBIT',$3)", txn_id, sys, amt)
        await conn.execute("INSERT INTO ledger_entries (id,transaction_id,account_id,entry_type,amount) VALUES (gen_random_uuid(),$1,$2,'CREDIT',$3)", txn_id, aid, amt)
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_locking_blocks_with_for_no_key_update(db_conn):
    u = await _new_user(db_conn)
    a = await _new_account(db_conn, u)
    b = await _new_account(db_conn, u)
    await _fund(db_conn, a, Decimal("10.00"))
    svc = LedgerService(LedgerRepository(db_conn))
    c2 = await asyncpg.connect(Settings().asyncpg_dsn)
    tr = c2.transaction()
    await tr.start()
    await c2.execute("SELECT id FROM accounts WHERE id = $1 FOR NO KEY UPDATE", a)
    task = asyncio.create_task(svc.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("1.00"), idempotency_key=f"k{uuid.uuid4().hex}"))
    done, pending = await asyncio.wait({task}, timeout=0.5)
    assert task in pending
    await tr.commit()
    res = await task
    assert res is not None
    ents = await db_conn.fetch("SELECT id FROM ledger_entries WHERE transaction_id = $1", uuid.UUID(res["transaction_id"]))
    assert len(ents) == 2
    await c2.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_twenty_concurrent_ten_succeed_stress(db_conn):
    for _ in range(30):
        u = await _new_user(db_conn)
        a = await _new_account(db_conn, u)
        b = await _new_account(db_conn, u)
        await _fund(db_conn, a, Decimal("10.00"))

        async def do_one(i):
            c = await asyncpg.connect(Settings().asyncpg_dsn)
            try:
                r = LedgerRepository(c)
                s = LedgerService(r)
                try:
                    await s.transfer(from_user_id=u, from_account_id=a, to_account_id=b, amount=Decimal("1.00"), idempotency_key=f"k{i}-{uuid.uuid4().hex}")
                    return "ok"
                except InsufficientFunds:
                    return "if"
                except Exception as e:
                    return e
            finally:
                await c.close()

        res = await asyncio.gather(*[do_one(i) for i in range(20)], return_exceptions=True)
        oks = [x for x in res if x == "ok"]
        ifs = [x for x in res if x == "if"]
        assert len(oks) == 10
        assert len(ifs) == 10
        repo = LedgerRepository(db_conn)
        ba = await repo.get_balance(a)
        bb = await repo.get_balance(b)
        assert ba >= Decimal("0")
        assert bb >= Decimal("0")
        assert ba == Decimal("0.00")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_transfers_stress(db_conn):
    # Calienta la resolucion de `localhost` una sola vez: `asyncio.wait_for`
    # cancela la tarea entera, y bajo carga (suite completa) el getaddrinfo
    # inicial puede tardar mas que el timeout aunque la transferencia en si no.
    _warm = await asyncpg.connect(Settings().asyncpg_dsn)
    await _warm.close()

    for _ in range(50):
        u1 = await _new_user(db_conn)
        a1 = await _new_account(db_conn, u1)
        u2 = await _new_user(db_conn)
        a2 = await _new_account(db_conn, u2)
        await _fund(db_conn, a1, Decimal("5.00"))
        await _fund(db_conn, a2, Decimal("5.00"))

        async def t1():
            c = await asyncpg.connect(Settings().asyncpg_dsn)
            try:
                return await LedgerService(LedgerRepository(c)).transfer(from_user_id=u1, from_account_id=a1, to_account_id=a2, amount=Decimal("1.00"), idempotency_key=f"x1-{uuid.uuid4().hex}")
            except Exception as e:
                if "Deadlock" in str(e):
                    raise
                return e
            finally:
                await c.close()

        async def t2():
            c = await asyncpg.connect(Settings().asyncpg_dsn)
            try:
                return await LedgerService(LedgerRepository(c)).transfer(from_user_id=u2, from_account_id=a2, to_account_id=a1, amount=Decimal("1.00"), idempotency_key=f"x2-{uuid.uuid4().hex}")
            except Exception as e:
                if "Deadlock" in str(e):
                    raise
                return e
            finally:
                await c.close()

        await asyncio.wait_for(asyncio.gather(t1(), t2(), return_exceptions=False), timeout=15)
