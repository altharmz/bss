"""Runtime log level: the log_setting singleton and its change audit (issue #274).

Revision ID: 0066
Revises: 0065

`log_setting` is read by the app, the worker and (through the app) the MCP connector every
15 s; DEBUG exists only as `debug_until`, so it always expires by itself. The 120-minute
maximum is enforced by the endpoint: a CHECK cannot express a duration relative to the
change time. `log_setting_change` is the append-only audit of every change.
"""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE log_setting (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            base_level text CHECK (base_level IN ('WARNING', 'INFO')),
            debug_until timestamptz,
            updated_at timestamptz,
            updated_by text
        );
        INSERT INTO log_setting (id) VALUES (true);
        COMMENT ON TABLE log_setting IS
            'The runtime log level (one row), polled every 15 s by the app, the worker and the MCP connector. Effective level: DEBUG while now < debug_until, else base_level, else the LOG_LEVEL environment value.';
        COMMENT ON COLUMN log_setting.id IS 'Always true: the table holds one row.';
        COMMENT ON COLUMN log_setting.base_level IS
            'WARNING or INFO; null follows the LOG_LEVEL environment value. DEBUG is never a base level.';
        COMMENT ON COLUMN log_setting.debug_until IS
            'DEBUG is in force until this instant (injected clock; half-open: at the instant it is over). Null or in the past: no debug. The endpoint allows at most 120 minutes ahead.';
        COMMENT ON COLUMN log_setting.updated_at IS 'Last change by an administrator (injected clock); null until the first.';
        COMMENT ON COLUMN log_setting.updated_by IS 'Username of that administrator.';

        CREATE TABLE log_setting_change (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            operator text NOT NULL,
            old_base_level text,
            new_base_level text,
            old_debug_until timestamptz,
            new_debug_until timestamptz,
            changed_at timestamptz NOT NULL
        );
        COMMENT ON TABLE log_setting_change IS
            'Append-only audit of log_setting changes: who, the level and debug expiry before and after, and when.';
        COMMENT ON COLUMN log_setting_change.id IS 'Surrogate key.';
        COMMENT ON COLUMN log_setting_change.operator IS 'Username of the administrator who changed the setting.';
        COMMENT ON COLUMN log_setting_change.old_base_level IS 'base_level before the change (null: follows LOG_LEVEL).';
        COMMENT ON COLUMN log_setting_change.new_base_level IS 'base_level after the change.';
        COMMENT ON COLUMN log_setting_change.old_debug_until IS 'debug_until before the change.';
        COMMENT ON COLUMN log_setting_change.new_debug_until IS 'debug_until after the change.';
        COMMENT ON COLUMN log_setting_change.changed_at IS 'When the change was made (injected clock).';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE log_setting_change; DROP TABLE log_setting;")
