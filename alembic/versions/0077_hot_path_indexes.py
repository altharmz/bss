"""Indexes for the worker hot paths, the admin list sorts and the unindexed references (#310).

Revision ID: 0077
Revises: 0076

From the EXPLAIN review of the 100k load-test dataset (#310) and the SQL plan sweep
(#327). Every index here was EXPLAINed before and after on that dataset (in the PR).

- **Worker hot paths**, run every wake-up or for every billed account: the ABRs of a
  product (both existing product_id indexes are partial), the auto top-up and renewal
  card candidates, the PAYG rating selectors (grantless top-level lines) and the
  notification detectors (each reads the rows since its watermark). An expression index
  for the no-renewal-yet anti-join on `request->>'previous_interval_id'` was measured and
  left out: its nested-loop probes were slower than the hash anti-join (the query's cost
  is the DISTINCT ON over every funded interval).
- **Admin list sorts**: each list's exact order, so a page reads the index instead of
  sorting the table. `ORDER BY created_at DESC, id` (payment, network commands,
  notifications) needs `(created_at DESC, id)`; `created_at DESC, id DESC` and
  `created_at, id` are served by `(created_at, id)`. `notification_message (created_at)`
  and `event_delivery (subscription_id)` are replaced by the wider indexes, which serve
  their old queries too (the retention batch scans the new one backwards).
- **Billing runs**: the `running` check every wake-up and the retention selector of the
  oldest completed run.
- **References without an index**: foreign keys whose referenced row can be deleted or
  whose PATCH guard asks "is it referenced" (`billing_run_account`, `usage_record` and
  `usage_charge` by account, the order item and order references, offerings ordered,
  payment methods used). `usage_record (billing_account_id, event_date)` also serves the
  per-account rating read.

Plain CREATE INDEX in the migration's transaction, as 0070: production data is small. On
the partitioned `usage_record` it creates the index on every partition, and on the ones
`usage_record_partition` creates later.
"""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

INDEXES = {
    # worker hot paths
    "applied_customer_billing_rate_product_id_idx": "applied_customer_billing_rate (product_id)",
    "billing_account_auto_topup_idx": "billing_account (id) WHERE auto_topup_enabled",
    "product_grantless_account_idx": (
        "product (billing_account_id) WHERE grantless AND parent_product_id IS NULL"
    ),
    "payment_command_renewal_idx": (
        "payment_command (renewal_command_id) WHERE renewal_command_id IS NOT NULL"
    ),
    "prepaid_command_renewed_idx": (
        "prepaid_command (attempted_at) WHERE kind = 'renewal' AND state = 'completed'"
    ),
    "usage_threshold_reached_idx": "usage_threshold (reached_at)",
    "payment_command_declined_idx": "payment_command (created_at) WHERE state = 'declined'",
    "network_command_sim_swapped_idx": (
        "network_command (confirmed_at) WHERE kind = 'swap_sim' AND state = 'confirmed'"
    ),
    # admin list sorts
    "applied_customer_billing_rate_created_at_id_idx": (
        "applied_customer_billing_rate (created_at, id)"
    ),
    "prepaid_command_created_at_id_idx": "prepaid_command (created_at, id)",
    "payment_command_created_at_id_idx": "payment_command (created_at DESC, id)",
    "network_command_created_at_id_idx": "network_command (created_at DESC, id)",
    "notification_message_created_at_id_idx": "notification_message (created_at DESC, id)",
    "payment_method_created_at_id_idx": "payment_method (created_at, id)",
    "event_delivery_subscription_last_idx": (
        "event_delivery (subscription_id, last_attempt_at DESC, id DESC)"
    ),
    # billing runs
    "billing_run_running_idx": "billing_run (id) WHERE status = 'running'",
    "billing_run_completed_idx": "billing_run (run_date, id) WHERE status = 'completed'",
    # references without an index
    "billing_run_account_billing_account_id_idx": "billing_run_account (billing_account_id)",
    "usage_record_account_date_idx": "usage_record (billing_account_id, event_date)",
    "usage_charge_billing_account_id_idx": "usage_charge (billing_account_id)",
    "port_request_billing_account_id_idx": (
        "port_request (billing_account_id) WHERE billing_account_id IS NOT NULL"
    ),
    "port_request_product_order_id_idx": (
        "port_request (product_order_id) WHERE product_order_id IS NOT NULL"
    ),
    "product_device_recorded_by_item_id_idx": "product_device (recorded_by_item_id)",
    "product_device_ended_by_item_id_idx": (
        "product_device (ended_by_item_id) WHERE ended_by_item_id IS NOT NULL"
    ),
    "product_order_error_message_product_order_item_id_idx": (
        "product_order_error_message (product_order_item_id)"
        " WHERE product_order_item_id IS NOT NULL"
    ),
    "prepaid_lifecycle_event_product_order_id_idx": (
        "prepaid_lifecycle_event (product_order_id) WHERE product_order_id IS NOT NULL"
    ),
    "product_order_item_product_offering_id_idx": "product_order_item (product_offering_id)",
    "payment_command_payment_method_id_idx": "payment_command (payment_method_id)",
}

# replaced by a wider index above
REPLACED = {
    "notification_message_created_idx": "notification_message (created_at)",
    "event_delivery_subscription_idx": "event_delivery (subscription_id)",
}


def upgrade() -> None:
    statements = [f"CREATE INDEX {name} ON {target};" for name, target in INDEXES.items()]
    statements += [f"DROP INDEX {name};" for name in REPLACED]
    op.execute("\n".join(statements))


def downgrade() -> None:
    statements = [f"CREATE INDEX {name} ON {target};" for name, target in REPLACED.items()]
    statements += [f"DROP INDEX {name};" for name in reversed(INDEXES)]
    op.execute("\n".join(statements))
