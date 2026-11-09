"""Indexes for the per-account paths that the 100k load-test seed found scanning (#292).

Revision ID: 0067
Revises: 0066

Measured on the seed at about 20k subscribers (docs/loadtest.md):

- `funding_conversion_source (billing_account_id)`: the funding-bounds part of the
  `balance_ledger_check` constraint trigger filters on the account and read the whole
  table on every prepaid write.
- `funding_hold_source (hold_id)`: holds read their sources by hold (`WHERE hold_id = %s`);
  only partial unique indexes started with `hold_id`, and their predicates do not match.
- `funded_interval (product_id, lower(period) DESC, authorized_at DESC)`: the latest grant
  of a product, read for every prepaid line by the entitlement, renewal and usage paths
  (`ORDER BY lower(period) DESC, authorized_at DESC LIMIT 1`).

Plain CREATE INDEX in the migration's transaction: production data is small.
"""

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX funding_conversion_source_billing_account_id_idx
            ON funding_conversion_source (billing_account_id);
        CREATE INDEX funding_hold_source_hold_id_idx ON funding_hold_source (hold_id);
        CREATE INDEX funded_interval_latest_idx
            ON funded_interval (product_id, lower(period) DESC, authorized_at DESC);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX funded_interval_latest_idx;
        DROP INDEX funding_hold_source_hold_id_idx;
        DROP INDEX funding_conversion_source_billing_account_id_idx;
    """)
