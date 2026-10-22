"""ledger extensions

Revision ID: 20261022_0004
Revises: 20261022_0003
Create Date: 2026-10-22 07:00:00.000000

"""

from alembic import op

revision = "20261022_0004"
down_revision = "20261022_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE ledger_transactions
        ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'transfer',
        ADD COLUMN IF NOT EXISTS request_hash TEXT
    """)
    op.execute("ALTER TABLE ledger_transactions DROP CONSTRAINT IF EXISTS ledger_transactions_kind_check")
    op.execute("ALTER TABLE ledger_transactions ADD CONSTRAINT ledger_transactions_kind_check CHECK (kind IN ('transfer','funding'))")
    op.execute("ALTER TABLE ledger_transactions DROP CONSTRAINT IF EXISTS ledger_transactions_user_account_pair_check")
    op.execute(
        "ALTER TABLE ledger_transactions ADD CONSTRAINT ledger_transactions_user_account_pair_check "
        "CHECK ((from_user_id IS NULL) = (from_account_id IS NULL))"
    )

    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_assert_non_negative_non_system()
    RETURNS TRIGGER AS $$
    DECLARE
        r RECORD;
        v_balance NUMERIC;
    BEGIN
        FOR r IN
            SELECT e.account_id, a.is_system
            FROM ledger_entries e
            JOIN accounts a ON a.id = e.account_id
            WHERE e.transaction_id = NEW.transaction_id
            GROUP BY e.account_id, a.is_system
        LOOP
            IF r.is_system THEN
                CONTINUE;
            END IF;
            SELECT COALESCE(SUM(CASE WHEN entry_type='CREDIT' THEN amount ELSE 0 END),0)
                   - COALESCE(SUM(CASE WHEN entry_type='DEBIT' THEN amount ELSE 0 END),0)
            INTO v_balance
            FROM ledger_entries
            WHERE account_id = r.account_id;
            IF v_balance < 0 THEN
                RAISE EXCEPTION 'insufficient funds: account % would have negative balance', r.account_id;
            END IF;
        END LOOP;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_non_negative_non_system ON ledger_entries")
    op.execute(
        "CREATE CONSTRAINT TRIGGER ledger_entries_non_negative_non_system AFTER INSERT ON ledger_entries "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_assert_non_negative_non_system()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_non_negative_non_system ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_assert_non_negative_non_system()")
    op.execute("ALTER TABLE ledger_transactions DROP CONSTRAINT IF EXISTS ledger_transactions_user_account_pair_check")
    op.execute("ALTER TABLE ledger_transactions DROP CONSTRAINT IF EXISTS ledger_transactions_kind_check")
    op.execute("ALTER TABLE ledger_transactions DROP COLUMN IF EXISTS request_hash")
    op.execute("ALTER TABLE ledger_transactions DROP COLUMN IF EXISTS kind")
