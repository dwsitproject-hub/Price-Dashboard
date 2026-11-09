"""Drop iam.password_history: the "not one of your last 5 passwords" rule was removed (PRD AUTH-05).

Keeping old password hashes with no rule that reads them is only a liability, so the table goes too.

Revision ID: 0003_drop_password_history
"""
from alembic import op

revision = "0003_drop_password_history"
down_revision = "0002_alembic_version_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.password_history;")


def downgrade() -> None:
    op.execute(r"""
CREATE TABLE iam.password_history (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id        uuid        NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
  password_hash  text        NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_password_history_user ON iam.password_history(user_id, created_at DESC);
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON iam.password_history TO aastar_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA iam TO aastar_app;
  END IF;
END $$;
""")
