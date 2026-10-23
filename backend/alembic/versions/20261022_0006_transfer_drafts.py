"""transfer drafts

Revision ID: 20261022_0006
Revises: 20261022_0005
Create Date: 2026-10-22 12:00:00.000000

Borradores de transferencia con confirmacion separada.

Reglas en base de datos:
- `amount NUMERIC(18,2) CHECK (amount > 0)`.
- `status` en ('pendiente','confirmada','rechazada','expirada').
- Origen != destino y ninguna cuenta de sistema.
- Trigger BEFORE UPDATE:
    (a) prohibe cambiar user_id, from_account_id, to_account_id, amount y expires_at;
    (b) solo permite transiciones desde 'pendiente';
    (c) exige ledger_transaction_id y confirmed_at al pasar a 'confirmada'.
- Triggers BEFORE DELETE (fila) y BEFORE TRUNCATE (sentencia): la tabla es
  append-only a nivel de borrado/truncado.
- `wallet_app` recibe SELECT, INSERT y UPDATE (nunca DELETE ni TRUNCATE).
"""

from alembic import op

revision = "20261022_0006"
down_revision = "20261022_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS transfer_drafts (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id),
        from_account_id UUID NOT NULL REFERENCES accounts(id),
        to_account_id UUID NOT NULL REFERENCES accounts(id),
        amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
        status TEXT NOT NULL DEFAULT 'pendiente'
            CHECK (status IN ('pendiente','confirmada','rechazada','expirada')),
        expires_at TIMESTAMPTZ NOT NULL,
        confirmed_at TIMESTAMPTZ,
        ledger_transaction_id UUID REFERENCES ledger_transactions(id),
        trace_id TEXT REFERENCES interactions(trace_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS transfer_drafts_user_id_idx ON transfer_drafts(user_id, created_at DESC, id DESC)"
    )
    op.execute(
        "ALTER TABLE transfer_drafts DROP CONSTRAINT IF EXISTS transfer_drafts_distinct_accounts_check"
    )
    op.execute(
        "ALTER TABLE transfer_drafts ADD CONSTRAINT transfer_drafts_distinct_accounts_check "
        "CHECK (from_account_id <> to_account_id)"
    )

    # ------------------------------------------------------------------
    # Origen/destino no pueden ser cuentas de sistema (validado por trigger
    # porque requiere consultar `accounts`).
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION transfer_drafts_assert_no_system_accounts()
    RETURNS TRIGGER AS $$
    DECLARE
        v_count INTEGER;
    BEGIN
        SELECT count(*) INTO v_count
        FROM accounts
        WHERE id IN (NEW.from_account_id, NEW.to_account_id) AND is_system = true;
        IF v_count > 0 THEN
            RAISE EXCEPTION 'transfer draft cannot use system accounts';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_no_system_accounts ON transfer_drafts")
    op.execute(
        "CREATE TRIGGER transfer_drafts_no_system_accounts BEFORE INSERT ON transfer_drafts "
        "FOR EACH ROW EXECUTE FUNCTION transfer_drafts_assert_no_system_accounts()"
    )

    # ------------------------------------------------------------------
    # Inmutabilidad + maquina de estados (BEFORE UPDATE)
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION transfer_drafts_guard_update()
    RETURNS TRIGGER AS $$
    BEGIN
        IF NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.from_account_id IS DISTINCT FROM OLD.from_account_id
           OR NEW.to_account_id IS DISTINCT FROM OLD.to_account_id
           OR NEW.amount IS DISTINCT FROM OLD.amount
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
            RAISE EXCEPTION 'transfer draft immutable fields cannot be changed';
        END IF;

        IF NEW.status IS DISTINCT FROM OLD.status THEN
            IF OLD.status <> 'pendiente' THEN
                RAISE EXCEPTION 'transfer draft can only transition from pendiente';
            END IF;
            IF NEW.status = 'confirmada'
               AND (NEW.ledger_transaction_id IS NULL OR NEW.confirmed_at IS NULL) THEN
                RAISE EXCEPTION 'confirmada requires ledger_transaction_id and confirmed_at';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_guard_update ON transfer_drafts")
    op.execute(
        "CREATE TRIGGER transfer_drafts_guard_update BEFORE UPDATE ON transfer_drafts "
        "FOR EACH ROW EXECUTE FUNCTION transfer_drafts_guard_update()"
    )

    # ------------------------------------------------------------------
    # Sin borrado fisico ni truncado
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION transfer_drafts_block_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'transfer_drafts is append-only: DELETE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_block_delete ON transfer_drafts")
    op.execute(
        "CREATE TRIGGER transfer_drafts_block_delete BEFORE DELETE ON transfer_drafts "
        "FOR EACH ROW EXECUTE FUNCTION transfer_drafts_block_delete()"
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION transfer_drafts_block_truncate()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'transfer_drafts is append-only: TRUNCATE not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_no_truncate ON transfer_drafts")
    op.execute(
        "CREATE TRIGGER transfer_drafts_no_truncate BEFORE TRUNCATE ON transfer_drafts "
        "FOR EACH STATEMENT EXECUTE FUNCTION transfer_drafts_block_truncate()"
    )

    # ------------------------------------------------------------------
    # Permisos: wallet_app solo SELECT, INSERT, UPDATE (nunca DELETE)
    # ------------------------------------------------------------------
    op.execute("REVOKE ALL ON TABLE transfer_drafts FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE transfer_drafts TO wallet_app")
    op.execute("REVOKE DELETE, TRUNCATE ON TABLE transfer_drafts FROM wallet_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE transfer_drafts FROM wallet_app")
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_no_truncate ON transfer_drafts")
    op.execute("DROP FUNCTION IF EXISTS transfer_drafts_block_truncate()")
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_block_delete ON transfer_drafts")
    op.execute("DROP FUNCTION IF EXISTS transfer_drafts_block_delete()")
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_guard_update ON transfer_drafts")
    op.execute("DROP FUNCTION IF EXISTS transfer_drafts_guard_update()")
    op.execute("DROP TRIGGER IF EXISTS transfer_drafts_no_system_accounts ON transfer_drafts")
    op.execute("DROP FUNCTION IF EXISTS transfer_drafts_assert_no_system_accounts()")
    op.execute("DROP INDEX IF EXISTS transfer_drafts_user_id_idx")
    op.execute("DROP TABLE IF EXISTS transfer_drafts")
