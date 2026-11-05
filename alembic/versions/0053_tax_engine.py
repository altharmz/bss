"""Service address on the billing account and the tax engine's transactions (issue #184).

Revision ID: 0053
Revises: 0052
"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE billing_account
            ADD COLUMN service_address jsonb CHECK (jsonb_typeof(service_address) = 'object');
        COMMENT ON COLUMN billing_account.service_address IS
            'The US place of primary use of the account''s prepaid lines (street1, street2, city, stateOrProvince, postCode, country), taken from the account''s contact of type serviceAddress; the tax engine taxes prepaid charges by it. Null: the flat price tax applies.';

        CREATE TABLE tax_transaction (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            applied_rate_id uuid NOT NULL UNIQUE REFERENCES applied_customer_billing_rate(id),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            quote_id text NOT NULL,
            state text NOT NULL DEFAULT 'committed' CHECK (state IN ('committed', 'voided')),
            address jsonb NOT NULL CHECK (jsonb_typeof(address) = 'object'),
            lines jsonb NOT NULL CHECK (jsonb_typeof(lines) = 'array'),
            committed_at timestamptz NOT NULL,
            voided_at timestamptz,
            CHECK ((state = 'voided') = (voided_at IS NOT NULL))
        );
        CREATE INDEX tax_transaction_account_idx ON tax_transaction (billing_account_id);
        COMMENT ON TABLE tax_transaction IS
            'The tax engine transaction behind one prepaid charge (ABR): committed when the charge is booked, voided when the whole charge is credited.';
        COMMENT ON COLUMN tax_transaction.id IS
            'Not the idempotency key; the key sent to the engine is derived from applied_rate_id.';
        COMMENT ON COLUMN tax_transaction.quote_id IS
            'The engine''s id of the quote that was committed.';
        COMMENT ON COLUMN tax_transaction.address IS
            'The service address the tax was computed for; a later address change never alters a booked charge.';
        COMMENT ON COLUMN tax_transaction.lines IS
            'The tax lines as the engine answered (jurisdiction, taxCategory, taxRate, taxAmount).';
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE tax_transaction;
        ALTER TABLE billing_account DROP COLUMN service_address;
    """)
