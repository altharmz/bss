"""Application users, sessions and security audit.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app_user (
            username text PRIMARY KEY CHECK (username ~ '^[A-Za-z0-9._@-]{1,64}$'),
            role text NOT NULL CHECK (role IN ('admin', 'user')),
            password_hash text NOT NULL,
            disabled boolean NOT NULL DEFAULT false,
            must_change_password boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            last_login_at timestamptz
        );
        CREATE TABLE app_session (
            token_hash text PRIMARY KEY,
            username text NOT NULL REFERENCES app_user,
            created_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL
        );
        CREATE INDEX app_session_username ON app_session (username);
        COMMENT ON TABLE app_user IS 'Application identity: admin or user; disabled, never deleted.';
        COMMENT ON TABLE app_session IS 'Revocable sessions; SHA-256 tokens only, never raw cookies.';
        CREATE TABLE app_user_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            username text NOT NULL REFERENCES app_user,
            operator text NOT NULL,
            action text NOT NULL,
            occurred_at timestamptz NOT NULL
        );
        COMMENT ON TABLE app_user_audit IS
            'Security changes only: operator, target, action and injected clock; no secrets.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE app_user_audit; DROP TABLE app_session; DROP TABLE app_user;")
