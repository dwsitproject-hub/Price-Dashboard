"""Drop iam.users.must_change_password: the forced change of temporary passwords was removed.

Users sign in through DWS Hub SSO; the "Set your password" step after a temporary password (PRD AUTH-09) is no
longer wanted. Password users can still change their password from the user menu.

Revision ID: 0005_drop_must_change_password
"""
from alembic import op

revision = "0005_drop_must_change_password"
down_revision = "0004_app_temp_privilege"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE iam.users DROP COLUMN IF EXISTS must_change_password;")


def downgrade() -> None:
    op.execute("ALTER TABLE iam.users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;")
