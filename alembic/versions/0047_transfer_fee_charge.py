"""A transfer's fee is an applied billing rate of its own (P4h2).

TMF678 makes `product` optional on an AppliedCustomerBillingRate and
`appliedBillingRateType` a free string, so a fee needs no product:

- `charge_type` gains `oneTime`.
- `product_id` and `product_offering_id` become nullable. They are null exactly for a
  `oneTime` rate; recurring and refund rates still require both.
- `balance_action_id` (unique, nullable) names the transfer a fee belongs to and is its
  identity: one fee per transfer. Exactly the `oneTime` rates carry it.
- A fee is a positive tax-inclusive Money over a one-day half-open period.

The unique `(product_id, charge_type, lower(period))` index and `abr_grant_identity`
ignore rows with a null product (NULLs are distinct), which is right: a fee is unique by
its action. Rolling back refuses while a fee exists; the enum value stays (PostgreSQL
cannot drop one).

Revision ID: 0047
Revises: 0046
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# The new value is compared as text: a new enum value cannot be used in the transaction
# that adds it.
FEE = "charge_type::text = 'oneTime'"


def upgrade():
    op.execute("ALTER TYPE charge_type ADD VALUE IF NOT EXISTS 'oneTime'")
    op.execute(f"""
        ALTER TABLE applied_customer_billing_rate
            ALTER COLUMN product_id DROP NOT NULL,
            ALTER COLUMN product_offering_id DROP NOT NULL,
            ADD COLUMN balance_action_id uuid UNIQUE REFERENCES balance_action(id),
            ADD CONSTRAINT abr_fee_scope CHECK (
                ({FEE}) = (product_id IS NULL)
                AND ({FEE}) = (product_offering_id IS NULL)
                AND ({FEE}) = (balance_action_id IS NOT NULL)),
            ADD CONSTRAINT abr_fee_amount CHECK (
                NOT ({FEE}) OR (amount > 0 AND refunds_rate_id IS NULL
                                AND upper(period) = lower(period) + 1));
        COMMENT ON COLUMN applied_customer_billing_rate.product_id IS
            'The product billed; null exactly for a oneTime rate (a transfer fee).';
        COMMENT ON COLUMN applied_customer_billing_rate.product_offering_id IS
            'The offering the ABR bills (a refund: its charge''s). Billing sets it; an insert'
            ' without one gets the product''s offering on the first day of period. Null exactly'
            ' for a oneTime rate.';
        COMMENT ON COLUMN applied_customer_billing_rate.balance_action_id IS
            'The transfer whose fee this oneTime rate is (one fee per transfer); null otherwise.';
        COMMENT ON COLUMN applied_customer_billing_rate.charge_type IS
            'recurring (TMF678 appliedBillingCharge, amount >= 0), refund (appliedBillingCredit,'
            ' amount < 0) or oneTime (appliedBillingCharge: a transfer fee, amount > 0, one day,'
            ' no product).';
    """)


def downgrade():
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM applied_customer_billing_rate WHERE {FEE}) THEN
                RAISE EXCEPTION 'transfer fee charges must be retained at revision 0047';
            END IF;
        END $$;
        ALTER TABLE applied_customer_billing_rate
            DROP CONSTRAINT abr_fee_amount,
            DROP CONSTRAINT abr_fee_scope,
            DROP COLUMN balance_action_id,
            ALTER COLUMN product_id SET NOT NULL,
            ALTER COLUMN product_offering_id SET NOT NULL;
        COMMENT ON COLUMN applied_customer_billing_rate.product_id IS NULL;
        COMMENT ON COLUMN applied_customer_billing_rate.product_offering_id IS
            'The offering the ABR bills (a refund: its charge''s). Billing sets it; an insert'
            ' without one gets the product''s offering on the first day of period.';
        COMMENT ON COLUMN applied_customer_billing_rate.charge_type IS
            'recurring (TMF678 appliedBillingCharge, amount >= 0) or refund (appliedBillingCredit, amount < 0).';
    """)
