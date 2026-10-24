"""recipient handles

Revision ID: 20261022_0007
Revises: 20261022_0006
Create Date: 2026-10-22 21:00:00.000000

Destinatarios con handles opacos (US2 tramo B1).

- `users.display_name` para poder buscar por nombre y enmascararlo.
- `recipient_handles`: handle aleatorio (22 chars de `secrets.token_urlsafe(16)`)
  emitido por un solicitante concreto para una cuenta destino concreta, con
  vencimiento de 15 minutos.
- La tabla es append-only: triggers BEFORE UPDATE/DELETE/TRUNCATE. Un handle no
  se consume mutando la fila (no hay `used_at`); su vigencia la define
  `expires_at` y el uso queda auditado.
- `wallet_app` recibe solo SELECT e INSERT.
"""

from alembic import op

revision = "20261022_0007"
down_revision = "20261022_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT")

    op.execute("""
    CREATE TABLE IF NOT EXISTS recipient_handles (
        id UUID PRIMARY KEY,
        handle TEXT NOT NULL UNIQUE,
        requester_user_id UUID NOT NULL REFERENCES users(id),
        target_account_id UUID NOT NULL REFERENCES accounts(id),
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS recipient_handles_requester_idx "
        "ON recipient_handles(requester_user_id, expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS recipient_handles_target_idx "
        "ON recipient_handles(target_account_id)"
    )

    # ------------------------------------------------------------------
    # Append-only: sin UPDATE, DELETE ni TRUNCATE.
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION recipient_handles_block_update()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'recipient_handles is append-only: UPDATE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_block_update ON recipient_handles")
    op.execute(
        "CREATE TRIGGER recipient_handles_block_update BEFORE UPDATE ON recipient_handles "
        "FOR EACH ROW EXECUTE FUNCTION recipient_handles_block_update()"
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION recipient_handles_block_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'recipient_handles is append-only: DELETE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_block_delete ON recipient_handles")
    op.execute(
        "CREATE TRIGGER recipient_handles_block_delete BEFORE DELETE ON recipient_handles "
        "FOR EACH ROW EXECUTE FUNCTION recipient_handles_block_delete()"
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION recipient_handles_block_truncate()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'recipient_handles is append-only: TRUNCATE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_no_truncate ON recipient_handles")
    op.execute(
        "CREATE TRIGGER recipient_handles_no_truncate BEFORE TRUNCATE ON recipient_handles "
        "FOR EACH STATEMENT EXECUTE FUNCTION recipient_handles_block_truncate()"
    )

    # ------------------------------------------------------------------
    # Permisos: wallet_app solo SELECT e INSERT.
    # ------------------------------------------------------------------
    op.execute("REVOKE ALL ON TABLE recipient_handles FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON TABLE recipient_handles TO wallet_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE recipient_handles FROM wallet_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE recipient_handles FROM wallet_app")
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_no_truncate ON recipient_handles")
    op.execute("DROP FUNCTION IF EXISTS recipient_handles_block_truncate()")
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_block_delete ON recipient_handles")
    op.execute("DROP FUNCTION IF EXISTS recipient_handles_block_delete()")
    op.execute("DROP TRIGGER IF EXISTS recipient_handles_block_update ON recipient_handles")
    op.execute("DROP FUNCTION IF EXISTS recipient_handles_block_update()")
    op.execute("DROP INDEX IF EXISTS recipient_handles_target_idx")
    op.execute("DROP INDEX IF EXISTS recipient_handles_requester_idx")
    op.execute("DROP TABLE IF EXISTS recipient_handles")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS display_name")
