"""Index for the balance action lists, newest first (#314).

Revision ID: 0074
Revises: 0073

The TMF654 topupBalance, adjustBalance, reserveBalance and transferBalance lists are
read newest requested first (decided by the human 2026-11-11), one kind at a time, and
the Balances screen pages them on the server. balance_action grows with prepaid traffic
and had no index for that order; `(kind, requested_at, id)` serves each kind's list read
backwards.
"""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX balance_action_kind_requested_idx"
        " ON balance_action (kind, requested_at, id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX balance_action_kind_requested_idx;")
