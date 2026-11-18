"""Production-readiness hardening (qa/architecture findings AR-18, AR-26).

- Index market.price_daily(run_id): the nightly housekeeping deletes pipeline runs older than 400 days, and the
  foreign key's ON DELETE SET NULL would otherwise scan the whole price table (~1 M rows) for every run deleted.
- The read-only role (reporting) loses SELECT on the audit log and on the stored integration tokens - it was
  granted on every ops table by 0001.

Revision ID: 0008_hardening
"""
from alembic import op

revision = "0008_hardening"
down_revision = "0007_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE INDEX IF NOT EXISTS ix_price_daily_run ON market.price_daily(run_id) WHERE run_id IS NOT NULL;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    REVOKE SELECT ON ops.audit_logs, ops.integration_tokens, ops.integration_secrets FROM aastar_ro;
  END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DROP INDEX IF EXISTS market.ix_price_daily_run;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    GRANT SELECT ON ops.audit_logs, ops.integration_tokens TO aastar_ro;
  END IF;
END $$;
""")
