"""Multi-line prepaid: line-count discounts (#196).

Revision ID: 0082
Revises: 0081

docs/decisions/prepaid.md "Multi-line discounts". A `discount` price whose unit of measure is
`lines` is a tier of a plan offering: `alteration_lines` is its minimum number of live plan
lines (N >= 2), it carries the plan's recurring period, and tiers of one offering and currency
do not overlap per N. The ABR keeps the discount it was charged with. Existing rows are
untouched.
"""

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE product_offering_price
            ADD COLUMN alteration_lines integer CHECK (alteration_lines >= 2),
            ADD CONSTRAINT product_offering_price_lines_are_discount
                CHECK (alteration_lines IS NULL OR price_type::text = 'discount'),
            DROP CONSTRAINT product_offering_price_recurring_has_period,
            ADD CONSTRAINT product_offering_price_recurring_has_period CHECK (
                (price_type = 'recurring' OR alteration_lines IS NOT NULL)
                = (recurring_period IS NOT NULL)),
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                (coalesce(usage_kind, '')) WITH =, (coalesce(alteration_lines, 0)) WITH =,
                valid_for WITH &&);
        COMMENT ON COLUMN product_offering_price.alteration_lines IS
            'For a multi-line discount (priceType discount, unitOfMeasure in lines): the minimum number of live plan lines on the account, 2 or more. The price is the amount off per line and period.';
        COMMENT ON COLUMN product_offering_price.recurring_period IS
            'TMF620 recurringChargePeriodType: daily, weekly, monthly or yearly. The charge period of a recurring price, and the period a multi-line discount applies to (the plan''s).';

        ALTER TABLE applied_customer_billing_rate
            ADD COLUMN discount_price_id uuid REFERENCES product_offering_price(id),
            ADD COLUMN discount_amount numeric(12,2) NOT NULL DEFAULT 0
                CHECK (discount_amount >= 0),
            ADD CONSTRAINT abr_discount_has_price
                CHECK ((discount_price_id IS NULL) = (discount_amount = 0));
        CREATE INDEX abr_discount_price_idx ON applied_customer_billing_rate (discount_price_id)
            WHERE discount_price_id IS NOT NULL;
        COMMENT ON COLUMN applied_customer_billing_rate.discount_price_id IS
            'The multi-line discount tier this recurring charge was quoted with, if any.';
        COMMENT ON COLUMN applied_customer_billing_rate.discount_amount IS
            'What the tier took off the recurring price; amount is already net of it (0 without a tier).';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM product_offering_price WHERE alteration_lines IS NOT NULL)
               OR EXISTS (SELECT FROM applied_customer_billing_rate
                          WHERE discount_price_id IS NOT NULL) THEN
                RAISE EXCEPTION 'multi-line discounts and their charges must be retained at revision 0082';
            END IF;
        END $$;
        DROP INDEX abr_discount_price_idx;
        ALTER TABLE applied_customer_billing_rate
            DROP CONSTRAINT abr_discount_has_price,
            DROP COLUMN discount_amount,
            DROP COLUMN discount_price_id;
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                (coalesce(usage_kind, '')) WITH =, valid_for WITH &&),
            DROP CONSTRAINT product_offering_price_recurring_has_period,
            ADD CONSTRAINT product_offering_price_recurring_has_period
                CHECK ((price_type = 'recurring') = (recurring_period IS NOT NULL)),
            DROP CONSTRAINT product_offering_price_lines_are_discount,
            DROP COLUMN alteration_lines;
        COMMENT ON COLUMN product_offering_price.recurring_period IS
            'TMF620 recurringChargePeriodType: daily, weekly, monthly or yearly.';
    """)
