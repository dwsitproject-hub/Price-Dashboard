"""Let the runtime roles read public.alembic_version (readiness probe).

GET /api/health/ready compares the applied revision with the one the code expects. On ApsaraDB the table is
created by aastar_owner with no grant to aastar_app, so the probe failed with "permission denied" (the local
Docker init script hid this through default privileges on schema public).

Revision ID: 0002_alembic_version_grant
"""
from alembic import op

revision = "0002_alembic_version_grant"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

DDL = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    GRANT SELECT ON public.alembic_version TO aastar_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    GRANT SELECT ON public.alembic_version TO aastar_ro;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.alembic_version FROM aastar_app, aastar_ro;")
