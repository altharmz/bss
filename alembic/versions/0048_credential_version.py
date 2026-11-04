"""app_user.credential_version: the MCP connector's grants die with the credential.

An OAuth grant of the MCP connector remembers the version it was issued for. The
version is incremented on a password change, an administrator's password reset, a role
change and a disable (the points that revoke sessions); a request for an `mcp:` principal
with another version is answered 401 credentialRevoked.

Revision ID: 0048
Revises: 0047
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE app_user ADD COLUMN credential_version integer NOT NULL DEFAULT 1;
        COMMENT ON COLUMN app_user.credential_version IS
            'Incremented on password change, password reset, role change and disable; '
            'an MCP OAuth grant issued for another version is revoked.';
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE app_user DROP COLUMN credential_version")
