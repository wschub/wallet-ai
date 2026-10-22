"""system account rules

Revision ID: 20261022_0005
Revises: 20261022_0004
Create Date: 2026-10-22 08:00:00.000000

"""

from alembic import op

revision = "20261022_0005"
down_revision = "20261022_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION ledger_assert_system_account_rules()
    RETURNS TRIGGER AS $$
    DECLARE
        v_kind TEXT;
    BEGIN
        SELECT lt.kind INTO v_kind FROM ledger_transactions lt WHERE lt.id = NEW.transaction_id;
        IF v_kind = 'transfer' THEN
            IF EXISTS (
                SELECT 1 FROM ledger_entries e
                JOIN accounts a ON a.id = e.account_id
                WHERE e.transaction_id = NEW.transaction_id AND a.is_system = true
            ) THEN
                RAISE EXCEPTION 'system account not allowed in transfer';
            END IF;
        ELSIF v_kind = 'funding' THEN
            IF EXISTS (
                SELECT 1 FROM ledger_entries e
                JOIN accounts a ON a.id = e.account_id
                WHERE e.transaction_id = NEW.transaction_id AND e.entry_type = 'DEBIT' AND a.is_system = false
            ) THEN
                RAISE EXCEPTION 'funding must debit from system account';
            END IF;
            IF EXISTS (
                SELECT 1 FROM ledger_entries e
                JOIN accounts a ON a.id = e.account_id
                WHERE e.transaction_id = NEW.transaction_id AND e.entry_type = 'CREDIT' AND a.is_system = true
            ) THEN
                RAISE EXCEPTION 'funding must credit to non-system account';
            END IF;
        END IF;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_system_account_rules ON ledger_entries")
    op.execute(
        "CREATE CONSTRAINT TRIGGER ledger_entries_system_account_rules AFTER INSERT ON ledger_entries "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_assert_system_account_rules()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_system_account_rules ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_assert_system_account_rules()")
