"""Grants by their end date, and the failed rows of the /metrics queues (#311).

Revision ID: 0079
Revises: 0078

From the EXPLAIN review of the 100k load-test dataset (#311); each EXPLAINed before and
after on that dataset (in the PR).

- `funded_interval (upper(period))`: the renewal producer (every wake-up) and the
  plan-expiring notification detector read the latest grant of each product, a DISTINCT ON
  over every funded interval, sorted: 0.5 s per wake-up, growing with the grant history.
  They now read the grants ending by today (the producer) or in the notice window (the
  detector) and keep those no later grant of the product follows
  (`entitlement.LATER_GRANT`, served by funded_interval_latest_idx). This index finds the
  grants ending in the window, so the detector reads only the window as history grows,
  and a wake-up with nothing due reads nothing (78 ms -> 0.4 ms).
- `/metrics` counts each queue's pending and failed rows with the condition in WHERE, so
  the queue's partial index serves it instead of a scan of every row ever queued. The
  pending conditions and the failed rows of prepaid_command and event_delivery already
  have one; these add the failed rows of network_command, payment_command and
  notification_message (a few rows each, rarely written).

Plain CREATE INDEX in the migration's transaction, as 0070: production data is small.
"""

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

INDEXES = {
    "funded_interval_end_idx": "funded_interval (upper(period))",
    "network_command_exhausted_idx": "network_command (created_at) WHERE state = 'exhausted'",
    "payment_command_exhausted_idx": "payment_command (created_at) WHERE state = 'exhausted'",
    "notification_message_failed_idx": (
        "notification_message (created_at) WHERE state = 'failed'"
    ),
}


def upgrade() -> None:
    op.execute("\n".join(f"CREATE INDEX {name} ON {target};" for name, target in INDEXES.items()))


def downgrade() -> None:
    op.execute("\n".join(f"DROP INDEX {name};" for name in reversed(INDEXES)))
