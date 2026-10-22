"""ledger double-entry tables

Revision ID: 20261022_0003
Revises: 20261021_0002
Create Date: 2026-10-22 06:00:00.000000

"""

from alembic import op

revision = "20261022_0003"
down_revision = "20261021_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # accounts.is_system: cuenta de sistema "fondeo" (contraparte del seed)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false")

    # ------------------------------------------------------------------
    # ledger_transactions (solo inserción)
    # ------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS ledger_transactions (
        id UUID PRIMARY KEY,
        from_user_id UUID REFERENCES users(id),
        from_account_id UUID REFERENCES accounts(id),
        idempotency_key TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ledger_transactions_idempotency_uniq
            UNIQUE NULLS NOT DISTINCT (from_user_id, from_account_id, idempotency_key)
    )
    """)

    # ------------------------------------------------------------------
    # ledger_entries (solo inserción)
    # ------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS ledger_entries (
        id UUID PRIMARY KEY,
        transaction_id UUID NOT NULL REFERENCES ledger_transactions(id),
        account_id UUID NOT NULL REFERENCES accounts(id),
        entry_type TEXT NOT NULL CHECK (entry_type IN ('DEBIT','CREDIT')),
        amount NUMERIC(18,2) NOT NULL CHECK (amount > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ledger_entries_transaction_id_idx ON ledger_entries(transaction_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ledger_entries_account_id_idx ON ledger_entries(account_id)")

    # ------------------------------------------------------------------
    # Inmutabilidad: UPDATE / DELETE (fila) y TRUNCATE (sentencia)
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_block_update()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only: UPDATE not allowed', TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_block_update ON ledger_transactions")
    op.execute(
        "CREATE TRIGGER ledger_transactions_block_update BEFORE UPDATE ON ledger_transactions "
        "FOR EACH ROW EXECUTE FUNCTION ledger_block_update()"
    )
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_block_update ON ledger_entries")
    op.execute(
        "CREATE TRIGGER ledger_entries_block_update BEFORE UPDATE ON ledger_entries "
        "FOR EACH ROW EXECUTE FUNCTION ledger_block_update()"
    )

    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_block_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only: DELETE not allowed', TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_block_delete ON ledger_transactions")
    op.execute(
        "CREATE TRIGGER ledger_transactions_block_delete BEFORE DELETE ON ledger_transactions "
        "FOR EACH ROW EXECUTE FUNCTION ledger_block_delete()"
    )
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_block_delete ON ledger_entries")
    op.execute(
        "CREATE TRIGGER ledger_entries_block_delete BEFORE DELETE ON ledger_entries "
        "FOR EACH ROW EXECUTE FUNCTION ledger_block_delete()"
    )

    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_block_truncate()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only: TRUNCATE not allowed', TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_no_truncate ON ledger_transactions")
    op.execute(
        "CREATE TRIGGER ledger_transactions_no_truncate BEFORE TRUNCATE ON ledger_transactions "
        "FOR EACH STATEMENT EXECUTE FUNCTION ledger_block_truncate()"
    )
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_no_truncate ON ledger_entries")
    op.execute(
        "CREATE TRIGGER ledger_entries_no_truncate BEFORE TRUNCATE ON ledger_entries "
        "FOR EACH STATEMENT EXECUTE FUNCTION ledger_block_truncate()"
    )

    # ------------------------------------------------------------------
    # Doble entrada: trigger diferido (por transacción)
    #   sum(DEBIT) = sum(CREDIT) y al menos un DEBIT y un CREDIT
    # ------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_assert_balanced(p_transaction_id UUID)
    RETURNS VOID AS $$
    DECLARE
        v_debit NUMERIC := 0;
        v_credit NUMERIC := 0;
        v_debit_count INTEGER := 0;
        v_credit_count INTEGER := 0;
    BEGIN
        SELECT
            COALESCE(SUM(amount) FILTER (WHERE entry_type = 'DEBIT'), 0),
            COALESCE(SUM(amount) FILTER (WHERE entry_type = 'CREDIT'), 0),
            COUNT(*) FILTER (WHERE entry_type = 'DEBIT'),
            COUNT(*) FILTER (WHERE entry_type = 'CREDIT')
        INTO v_debit, v_credit, v_debit_count, v_credit_count
        FROM ledger_entries
        WHERE transaction_id = p_transaction_id;

        IF v_debit_count = 0 OR v_credit_count = 0 THEN
            RAISE EXCEPTION
                'ledger transaction % is unbalanced: needs at least one DEBIT and one CREDIT entry',
                p_transaction_id;
        END IF;

        IF v_debit <> v_credit THEN
            RAISE EXCEPTION
                'ledger transaction % is unbalanced: DEBIT sum % <> CREDIT sum %',
                p_transaction_id, v_debit, v_credit;
        END IF;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_transactions_assert_balanced()
    RETURNS TRIGGER AS $$
    BEGIN
        PERFORM ledger_assert_balanced(NEW.id);
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_entries_assert_balanced()
    RETURNS TRIGGER AS $$
    BEGIN
        PERFORM ledger_assert_balanced(NEW.transaction_id);
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_balanced ON ledger_transactions")
    op.execute(
        "CREATE CONSTRAINT TRIGGER ledger_transactions_balanced AFTER INSERT ON ledger_transactions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_transactions_assert_balanced()"
    )
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries")
    op.execute(
        "CREATE CONSTRAINT TRIGGER ledger_entries_balanced AFTER INSERT ON ledger_entries "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_entries_assert_balanced()"
    )

    # ------------------------------------------------------------------
    # Permisos: wallet_app solo INSERT + SELECT
    # ------------------------------------------------------------------
    op.execute("REVOKE ALL ON TABLE ledger_transactions FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE ledger_entries FROM PUBLIC")
    op.execute("GRANT INSERT, SELECT ON TABLE ledger_transactions TO wallet_app")
    op.execute("GRANT INSERT, SELECT ON TABLE ledger_entries TO wallet_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE ledger_transactions FROM wallet_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE ledger_entries FROM wallet_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE ledger_entries FROM wallet_app")
    op.execute("REVOKE ALL ON TABLE ledger_transactions FROM wallet_app")

    op.execute("DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_balanced ON ledger_transactions")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_assert_balanced()")
    op.execute("DROP FUNCTION IF EXISTS ledger_transactions_assert_balanced()")
    op.execute("DROP FUNCTION IF EXISTS ledger_assert_balanced(UUID)")

    op.execute("DROP TRIGGER IF EXISTS ledger_entries_no_truncate ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_no_truncate ON ledger_transactions")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_block_delete ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_block_delete ON ledger_transactions")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_block_update ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_block_update ON ledger_transactions")
    op.execute("DROP FUNCTION IF EXISTS ledger_block_truncate()")
    op.execute("DROP FUNCTION IF EXISTS ledger_block_delete()")
    op.execute("DROP FUNCTION IF EXISTS ledger_block_update()")

    op.execute("DROP INDEX IF EXISTS ledger_entries_account_id_idx")
    op.execute("DROP INDEX IF EXISTS ledger_entries_transaction_id_idx")
    op.execute("DROP TABLE IF EXISTS ledger_entries")
    op.execute("DROP TABLE IF EXISTS ledger_transactions")

    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS is_system")
