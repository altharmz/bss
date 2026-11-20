"""Pooled data per account (#379).

Revision ID: 0089
Revises: 0088

docs/decisions/usage.md "Pooled data", network.md "Data pool commands".

- `usage_pool_threshold`: the 80% and 100% marks of an account's data pool, once per account,
  pool period and threshold (the pool's `usage_threshold`).
- `network_command.purpose` adds `dataPool`: the change_profile that tells a pool member the
  pool's new size. It belongs to no order item, so `product_order_id` and
  `product_order_item_id` are null exactly for it.
- `network_command.data_pool`: the pool fields the command carried (`id`, `mb`,
  `periodStart`), set when a member's activate or change_profile is first sent and on every
  `dataPool` command.
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE usage_pool_threshold (
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            period_start date NOT NULL,
            threshold smallint NOT NULL CHECK (threshold IN (80, 100)),
            period_end date NOT NULL CHECK (period_end > period_start),
            product_id uuid NOT NULL REFERENCES product(id),
            usage_record_id uuid NOT NULL,
            used bigint NOT NULL,
            included bigint NOT NULL CHECK (included > 0),
            reached_at timestamptz NOT NULL,
            PRIMARY KEY (billing_account_id, period_start, threshold)
        );
        CREATE INDEX usage_pool_threshold_reached_idx ON usage_pool_threshold (reached_at);
        COMMENT ON TABLE usage_pool_threshold IS
            'A data pool threshold (80% or 100%) reached by an account in a pool period: one row is one event, once per account, period and threshold. The notifications slice reads it.';
        COMMENT ON COLUMN usage_pool_threshold.period_start IS
            'First date of the pool period: the billing month starting on the account''s bill cycle day.';
        COMMENT ON COLUMN usage_pool_threshold.product_id IS
            'The pool member whose record reached the threshold; the message names its line.';
        COMMENT ON COLUMN usage_pool_threshold.usage_record_id IS
            'The record that reached it (no foreign key: usage_record is partitioned and old partitions are dropped).';
        COMMENT ON COLUMN usage_pool_threshold.used IS
            'Domestic data of every member in the period when the threshold was reached, in bytes.';
        COMMENT ON COLUMN usage_pool_threshold.included IS
            'The pool size on the record''s date, in bytes (MB x 1048576).';

        ALTER TABLE network_command
            DROP CONSTRAINT network_command_purpose_check,
            ADD CONSTRAINT network_command_purpose_check
                CHECK (purpose IN ('order', 'passEnd', 'dataPool')),
            ALTER COLUMN product_order_id DROP NOT NULL,
            ALTER COLUMN product_order_item_id DROP NOT NULL,
            ADD CONSTRAINT network_command_order_unless_pool CHECK (
                (purpose = 'dataPool')
                = (product_order_id IS NULL AND product_order_item_id IS NULL)),
            ADD COLUMN data_pool jsonb CHECK (jsonb_typeof(data_pool) = 'object'),
            ADD CONSTRAINT network_command_pool_carries_pool
                CHECK (purpose <> 'dataPool' OR (data_pool IS NOT NULL AND product_id IS NOT NULL));
        COMMENT ON COLUMN network_command.purpose IS
            'order: the command of its order item; passEnd: the change_profile on the day a pass (an add-on with passDays) ends before its plan period, with the plan profile and the add-ons still live; dataPool: the change_profile that tells a pool member the pool''s new size (no order).';
        COMMENT ON COLUMN network_command.data_pool IS
            'The data pool the command carried: {id (the billing account), mb (the pool size), periodStart}; set at the first send of a member''s activate or change_profile, and on every dataPool command. Null for a line outside a pool.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM network_command WHERE purpose = 'dataPool')
               OR EXISTS (SELECT FROM usage_pool_threshold) THEN
                RAISE EXCEPTION 'data pool commands and thresholds must be retained at revision 0088';
            END IF;
        END $$;
        ALTER TABLE network_command
            DROP CONSTRAINT network_command_pool_carries_pool,
            DROP COLUMN data_pool,
            DROP CONSTRAINT network_command_order_unless_pool,
            ALTER COLUMN product_order_item_id SET NOT NULL,
            ALTER COLUMN product_order_id SET NOT NULL,
            DROP CONSTRAINT network_command_purpose_check,
            ADD CONSTRAINT network_command_purpose_check CHECK (purpose IN ('order', 'passEnd'));
        DROP TABLE usage_pool_threshold;
    """)
