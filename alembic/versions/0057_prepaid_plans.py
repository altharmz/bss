"""Prepaid plans: one-time fees on products, add-on and pay-as-you-go products, card renewals
(issue #187).

- An ABR of charge type `oneTime` may now belong to a product: the activation fee and the
  fees of an add-on, from the offering's `oneTime` prices. It carries the price
  (`one_time_price_id`) and the price's name (`charge_name`); a transfer fee keeps its
  balance action and no product.
- `product.parent_product_id` (an add-on's plan product) and `product.grantless` (an add-on or
  a pay-as-you-go product: active between its dates with no funded interval, never renewed).
- `payment_command.purpose` gains `renewal`: the card charge for a renewal's shortfall.

Revision ID: 0057
Revises: 0056
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

FEE = "charge_type::text = 'oneTime'"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE applied_customer_billing_rate
            ADD COLUMN one_time_price_id uuid REFERENCES product_offering_price(id),
            ADD COLUMN charge_name text,
            DROP CONSTRAINT abr_fee_scope,
            ADD CONSTRAINT abr_fee_scope CHECK (
                (NOT ({FEE}) AND product_id IS NOT NULL AND product_offering_id IS NOT NULL
                    AND balance_action_id IS NULL AND one_time_price_id IS NULL)
                OR (({FEE}) AND balance_action_id IS NOT NULL AND product_id IS NULL
                    AND product_offering_id IS NULL AND one_time_price_id IS NULL)
                OR (({FEE}) AND balance_action_id IS NULL AND product_id IS NOT NULL
                    AND product_offering_id IS NOT NULL AND one_time_price_id IS NOT NULL)),
            ADD CONSTRAINT abr_fee_name CHECK ((one_time_price_id IS NULL) = (charge_name IS NULL));
        DROP INDEX abr_one_per_start;
        CREATE UNIQUE INDEX abr_one_per_start
            ON applied_customer_billing_rate (product_id, charge_type, period, product_offering_id)
            WHERE one_time_price_id IS NULL;
        CREATE UNIQUE INDEX abr_fee_once
            ON applied_customer_billing_rate (product_id, one_time_price_id, period)
            WHERE one_time_price_id IS NOT NULL;
        COMMENT ON COLUMN applied_customer_billing_rate.one_time_price_id IS
            'The oneTime catalog price a product fee (activation fee, add-on) charges; null for every other rate. One fee per product, price and day.';
        COMMENT ON COLUMN applied_customer_billing_rate.charge_name IS
            'The name a product fee is shown under (its price''s name at the time); null for every other rate.';
        COMMENT ON COLUMN applied_customer_billing_rate.product_id IS
            'The product billed; null exactly for a transfer fee (a oneTime rate of a balance action).';
        COMMENT ON COLUMN applied_customer_billing_rate.charge_type IS
            'recurring (TMF678 appliedBillingCharge, amount >= 0), refund (appliedBillingCredit, amount < 0) or oneTime (appliedBillingCharge: a transfer fee, or a product fee from a oneTime price; amount > 0, one day).';

        ALTER TABLE product
            ADD COLUMN parent_product_id uuid,
            ADD COLUMN grantless boolean NOT NULL DEFAULT false,
            ADD CONSTRAINT product_parent_fkey FOREIGN KEY (parent_product_id, billing_account_id)
                REFERENCES product (id, billing_account_id),
            ADD CONSTRAINT product_parent_grantless CHECK (parent_product_id IS NULL OR grantless);
        CREATE INDEX product_parent_idx ON product (parent_product_id)
            WHERE parent_product_id IS NOT NULL;
        COMMENT ON COLUMN product.parent_product_id IS
            'An add-on pack''s plan product (TMF637 productRelationship, type addOn); the add-on ends with the plan''s paid period.';
        COMMENT ON COLUMN product.grantless IS
            'Prepaid: an add-on or pay-as-you-go product. Active between its dates with no funded interval, never renewed; the order that created it completes it.';

        ALTER TABLE payment_command
            DROP CONSTRAINT payment_command_purpose_check,
            ADD CONSTRAINT payment_command_purpose_check
                CHECK (purpose IN ('topup', 'auto_topup', 'renewal')),
            ADD COLUMN renewal_command_id uuid REFERENCES prepaid_command(id);
        CREATE UNIQUE INDEX payment_command_one_renewal_in_flight
            ON payment_command (billing_account_id)
            WHERE purpose = 'renewal' AND state IN ('pending', 'sent');
        COMMENT ON COLUMN payment_command.renewal_command_id IS
            'For a renewal charge: the prepaid renewal command whose shortfall it covers.';
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM applied_customer_billing_rate
                       WHERE one_time_price_id IS NOT NULL)
               OR EXISTS (SELECT FROM product WHERE grantless)
               OR EXISTS (SELECT FROM payment_command WHERE purpose = 'renewal') THEN
                RAISE EXCEPTION 'plan fees, add-ons and renewal charges must be retained at revision 0057';
            END IF;
        END $$;
        DROP INDEX payment_command_one_renewal_in_flight;
        ALTER TABLE payment_command
            DROP COLUMN renewal_command_id,
            DROP CONSTRAINT payment_command_purpose_check,
            ADD CONSTRAINT payment_command_purpose_check
                CHECK (purpose IN ('topup', 'auto_topup'));
        DROP INDEX product_parent_idx;
        ALTER TABLE product
            DROP CONSTRAINT product_parent_grantless,
            DROP CONSTRAINT product_parent_fkey,
            DROP COLUMN grantless,
            DROP COLUMN parent_product_id;
        DROP INDEX abr_fee_once;
        DROP INDEX abr_one_per_start;
        CREATE UNIQUE INDEX abr_one_per_start
            ON applied_customer_billing_rate (product_id, charge_type, period, product_offering_id);
        ALTER TABLE applied_customer_billing_rate
            DROP CONSTRAINT abr_fee_name,
            DROP CONSTRAINT abr_fee_scope,
            ADD CONSTRAINT abr_fee_scope CHECK (
                ({FEE}) = (product_id IS NULL)
                AND ({FEE}) = (product_offering_id IS NULL)
                AND ({FEE}) = (balance_action_id IS NOT NULL)),
            DROP COLUMN charge_name,
            DROP COLUMN one_time_price_id;
    """)
