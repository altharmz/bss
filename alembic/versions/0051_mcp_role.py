"""app_user.role 'mcp': the only role the MCP connector's OAuth login accepts.

An `mcp` user cannot sign in to the UI and an `admin` or `user` cannot log in through
MCP. An `mcp` user's password is the one an administrator sets (it cannot do the UI's
forced first change), so such a row never carries must_change_password.

Revision ID: 0051
Revises: 0050
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE app_user DROP CONSTRAINT app_user_role_check;
        ALTER TABLE app_user ADD CONSTRAINT app_user_role_check
            CHECK (role IN ('admin', 'user', 'mcp'));
        ALTER TABLE app_user ADD CONSTRAINT app_user_mcp_password_set
            CHECK (role <> 'mcp' OR NOT must_change_password);
        COMMENT ON TABLE app_user IS
            'Application identity: admin or user (UI), mcp (MCP login only); '
            'disabled, never deleted.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE app_user DROP CONSTRAINT app_user_mcp_password_set;
        ALTER TABLE app_user DROP CONSTRAINT app_user_role_check;
        ALTER TABLE app_user ADD CONSTRAINT app_user_role_check
            CHECK (role IN ('admin', 'user'));
        COMMENT ON TABLE app_user IS 'Application identity: admin or user; disabled, never deleted.';
    """)
