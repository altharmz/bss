"""Configurable product recurring charge intervals.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE product_offering_price ALTER COLUMN recurring_period TYPE text;
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_recurring_period_length_check,
            ADD CONSTRAINT product_offering_price_recurring_period_length_check
                CHECK (recurring_period_length BETWEEN 1 AND 1000),
            ADD CONSTRAINT product_offering_price_recurring_period_check
                CHECK (recurring_period IN ('daily', 'weekly', 'monthly', 'yearly'));
        COMMENT ON COLUMN product_offering_price.recurring_period IS
            'TMF620 recurringChargePeriodType: daily, weekly, monthly or yearly.';
        COMMENT ON COLUMN product_offering_price.recurring_period_length IS
            'TMF620 recurringChargePeriodLength: positive interval multiplier.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM product_offering_price
                       WHERE recurring_period IS DISTINCT FROM 'monthly'
                         AND recurring_period IS NOT NULL
                          OR recurring_period_length <> 1) THEN
                RAISE EXCEPTION 'non-monthly prices must be removed before downgrade';
            END IF;
        END $$;
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_recurring_period_check,
            DROP CONSTRAINT product_offering_price_recurring_period_length_check,
            ADD CONSTRAINT product_offering_price_recurring_period_length_check
                CHECK (recurring_period_length = 1);
        ALTER TABLE product_offering_price ALTER COLUMN recurring_period
            TYPE recurring_charge_period_type USING recurring_period::recurring_charge_period_type;
    """)
