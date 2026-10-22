import uuid

import asyncpg
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wallet_app_role_permissions(db_conn):
    tid = f"t-{uuid.uuid4().hex}"
    await db_conn.execute(
        "INSERT INTO interactions (id, trace_id, user_id, channel, created_at) VALUES ($1,$2,$3,$4,now()) ON CONFLICT DO NOTHING",
        uuid.uuid4(),
        tid,
        None,
        "test",
    )
    await db_conn.execute("SET ROLE wallet_app")
    try:
        await db_conn.execute(
            "INSERT INTO audit_events (id, trace_id, user_id, event_type, payload) VALUES ($1,$2,$3,$4,$5)",
            uuid.uuid4(),
            tid,
            None,
            "llm_call",
            "{}",
        )
        rows = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
        assert len(rows) >= 1
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("UPDATE audit_events SET reason='x' WHERE trace_id = $1", tid)
        rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
        assert len(rows_after) >= 1
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("DELETE FROM audit_events WHERE trace_id = $1", tid)
        rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
        assert len(rows_after) >= 1
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("TRUNCATE TABLE audit_events")
        rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
        assert len(rows_after) >= 1
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("ALTER TABLE audit_events DISABLE TRIGGER ALL")
        rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
        assert len(rows_after) >= 1
    finally:
        await db_conn.execute("RESET ROLE")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_triggers_reject_by_default_role(db_conn):
    tid = f"t-{uuid.uuid4().hex}"
    await db_conn.execute(
        "INSERT INTO interactions (id, trace_id, user_id, channel, created_at) VALUES ($1,$2,$3,$4,now()) ON CONFLICT DO NOTHING",
        uuid.uuid4(),
        tid,
        None,
        "test",
    )
    await db_conn.execute(
        "INSERT INTO audit_events (id, trace_id, user_id, event_type, payload) VALUES ($1,$2,$3,$4,$5)",
        uuid.uuid4(),
        tid,
        None,
        "llm_call",
        "{}",
    )
    with pytest.raises(Exception, match="append-only"):
        await db_conn.execute("UPDATE audit_events SET reason='x' WHERE trace_id = $1", tid)
    rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
    assert len(rows_after) >= 1
    with pytest.raises(Exception, match="append-only"):
        await db_conn.execute("DELETE FROM audit_events WHERE trace_id = $1", tid)
    rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
    assert len(rows_after) >= 1
    with pytest.raises(Exception, match="append-only"):
        await db_conn.execute("TRUNCATE TABLE audit_events")
    rows_after = await db_conn.fetch("SELECT id FROM audit_events WHERE trace_id = $1", tid)
    assert len(rows_after) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_truncate_users_cascade_is_blocked(db_conn):
    tr = db_conn.transaction()
    await tr.start()
    try:
        with pytest.raises(Exception, match="append-only"):
            await db_conn.execute("TRUNCATE users CASCADE")
    finally:
        await tr.rollback()
