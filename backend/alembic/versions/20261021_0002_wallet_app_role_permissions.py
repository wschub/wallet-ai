"""wallet_app role permissions

Revision ID: 20261021_0002
Revises: 20261021_0001
Create Date: 2026-10-21 01:00:00.000000

"""
from alembic import op

revision = "20261021_0002"
down_revision = "20261021_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DO $$ BEGIN CREATE ROLE wallet_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("REVOKE ALL ON TABLE audit_events FROM PUBLIC")
    op.execute("GRANT INSERT, SELECT ON TABLE audit_events TO wallet_app")
    op.execute("GRANT SELECT ON TABLE users TO wallet_app")
    op.execute("GRANT SELECT ON TABLE accounts TO wallet_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_events FROM wallet_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE accounts FROM wallet_app")
    op.execute("REVOKE SELECT ON TABLE users FROM wallet_app")
    op.execute("REVOKE INSERT, SELECT ON TABLE audit_events FROM wallet_app")
    op.execute("GRANT ALL ON TABLE audit_events TO PUBLIC")
    op.execute("DO $$ BEGIN DROP ROLE wallet_app; EXCEPTION WHEN dependent_objects_still_exist THEN null; WHEN others THEN null; END $$;")
