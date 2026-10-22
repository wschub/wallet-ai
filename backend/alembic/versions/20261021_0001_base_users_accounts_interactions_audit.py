"""base users accounts interactions audit

Revision ID: 20261021_0001
Revises:
Create Date: 2026-10-21 00:00:00.000000

"""

from alembic import op

revision = "20261021_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('cliente','soporte','compliance')),
        password_hash TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id),
        currency TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS interactions (
        id UUID PRIMARY KEY,
        trace_id TEXT UNIQUE NOT NULL,
        user_id UUID REFERENCES users(id),
        channel TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_events (
        id UUID PRIMARY KEY,
        trace_id TEXT REFERENCES interactions(trace_id),
        user_id UUID REFERENCES users(id),
        event_type TEXT NOT NULL CHECK (event_type IN ('retrieval','tool_call','guardrail_block','llm_call','response')),
        payload JSONB NOT NULL,
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION audit_events_block_update()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events are append-only: UPDATE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS audit_events_block_update ON audit_events")
    op.execute(
        "CREATE TRIGGER audit_events_block_update BEFORE UPDATE ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_events_block_update()"
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION audit_events_block_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events are append-only: DELETE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS audit_events_block_delete ON audit_events")
    op.execute(
        "CREATE TRIGGER audit_events_block_delete BEFORE DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_events_block_delete()"
    )
    op.execute(
        """
    CREATE OR REPLACE FUNCTION audit_events_block_truncate()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events are append-only: TRUNCATE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """
    )
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_truncate()"
    )
    op.execute("DROP EVENT TRIGGER IF EXISTS audit_events_block_truncate_evt")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_truncate_func()")




def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_truncate()")
    op.execute("DROP EVENT TRIGGER IF EXISTS audit_events_block_truncate_evt")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_truncate_func()")
    op.execute("DROP TRIGGER IF EXISTS audit_events_block_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_delete()")
    op.execute("DROP TRIGGER IF EXISTS audit_events_block_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_update()")
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS interactions")
    op.execute("DROP TABLE IF EXISTS accounts")
    op.execute("DROP TABLE IF EXISTS users")
