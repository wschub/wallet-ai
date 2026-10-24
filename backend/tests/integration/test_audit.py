import uuid

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_append_only(db_conn):
    tid = uuid.uuid4().hex
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
        await db_conn.execute("UPDATE audit_events SET reason='x'")
    with pytest.raises(Exception, match="append-only"):
        await db_conn.execute("DELETE FROM audit_events")
    with pytest.raises(Exception, match="append-only"):
        await db_conn.execute("TRUNCATE TABLE audit_events")
    with pytest.raises(Exception):
        await db_conn.execute("TRUNCATE TABLE users CASCADE")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_payload_masked(db_conn):
    import json
    import uuid

    payload = {"card": "4111111111111111", "msg": "test"}
    from wallet_ai.infrastructure.pii.masker import PIIMasker

    masked = json.loads(PIIMasker().mask(json.dumps(payload)))
    tid = uuid.uuid4().hex
    await db_conn.execute(
        "INSERT INTO interactions (id, trace_id, user_id, channel, created_at) VALUES ($1,$2,$3,$4,now()) ON CONFLICT DO NOTHING",
        uuid.uuid4(),
        tid,
        None,
        "test",
    )
    await db_conn.execute(
        "INSERT INTO audit_events (id, trace_id, user_id, event_type, payload, reason) VALUES ($1,$2,$3,$4,$5,$6)",
        uuid.uuid4(),
        tid,
        None,
        "llm_call",
        json.dumps(masked),
        None,
    )

    row = await db_conn.fetchrow("SELECT payload::text as p FROM audit_events WHERE trace_id = $1", tid)
    assert "4111111111111111" not in row["p"]


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
