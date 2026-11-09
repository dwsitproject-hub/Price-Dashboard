"""Let aastar_app create temporary tables (pipeline bulk loads).

The pipeline COPYs each batch of prices / series points into a temporary table and upserts from there
(pipeline/store.py). deploy/rds/bootstrap_roles.sql revokes ALL on the database from PUBLIC, which also takes
away PostgreSQL's default TEMPORARY privilege, so on ApsaraDB every pipeline run failed with
"permission denied to create temporary tables". The owner (who runs migrations) grants it back explicitly.

Revision ID: 0004_app_temp_privilege
"""
from alembic import op

revision = "0004_app_temp_privilege"
down_revision = "0003_drop_password_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO aastar_app', current_database());
  END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM aastar_app', current_database());
  END IF;
END $$;
""")
