"""The sign-in throttle's attempts, shared by every app process (#288).

Revision ID: 0069
Revises: 0068

The throttle used to count in process memory, so with WEB_CONCURRENCY app processes the
limit became WEB_CONCURRENCY x 20 a minute. Keys, limits and messages are unchanged
(app/internal/users.py `limit_login`); rows older than a minute are deleted in the same
transaction as each attempt, through the `at` index.
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sign_in_attempt (
            key text NOT NULL CHECK (key <> ''),
            at timestamptz NOT NULL
        );
        CREATE INDEX sign_in_attempt_key_at_idx ON sign_in_attempt (key, at);
        CREATE INDEX sign_in_attempt_at_idx ON sign_in_attempt (at);
        COMMENT ON TABLE sign_in_attempt IS
            'Sign-in attempts of the last minute, one row per throttle key and attempt (20 a minute per user and address and per address). Rows older than a minute are deleted with each attempt.';
        COMMENT ON COLUMN sign_in_attempt.key IS
            'The throttle key: user|<username, case-folded>|<address> or address|<address>.';
        COMMENT ON COLUMN sign_in_attempt.at IS 'When the attempt was made (injected clock).';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE sign_in_attempt;")
