"""Persist account mode and stable prepaid balance identities (creation stays gated).

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE billing_account
            ADD COLUMN rating_type text NOT NULL DEFAULT 'postpaid',
            ADD CONSTRAINT billing_account_rating_type
                CHECK (rating_type IN ('prepaid', 'postpaid')),
            ADD CONSTRAINT billing_account_prepaid_credit_limit
                CHECK (rating_type <> 'prepaid' OR coalesce(credit_limit, 0) = 0),
            ADD CONSTRAINT billing_account_mode_identity UNIQUE (id, currency, rating_type);
        COMMENT ON COLUMN billing_account.rating_type IS
            'TMF666 account funding mode; existing accounts remain postpaid. Prepaid creation is not yet exposed.';

        CREATE TABLE prepaid_bucket (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            accumulated_balance_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            rating_type text NOT NULL DEFAULT 'prepaid' CHECK (rating_type = 'prepaid'),
            created_at timestamptz NOT NULL,
            UNIQUE (billing_account_id, currency),
            UNIQUE (id, billing_account_id, currency),
            FOREIGN KEY (billing_account_id, currency, rating_type)
                REFERENCES billing_account (id, currency, rating_type)
        );
        COMMENT ON TABLE prepaid_bucket IS
            'Stable TMF654 bucket and accumulatedBalance identities for one prepaid account/currency. No stored balance; funding ledger is introduced separately.';
        COMMENT ON COLUMN prepaid_bucket.id IS 'Stable TMF654 bucket identity.';
        COMMENT ON COLUMN prepaid_bucket.accumulated_balance_id IS
            'Stable TMF654 accumulatedBalance identity; one bucket per account/currency.';
        COMMENT ON COLUMN prepaid_bucket.billing_account_id IS 'Owning prepaid billing account.';
        COMMENT ON COLUMN prepaid_bucket.currency IS 'Account currency, enforced by a composite foreign key.';
        COMMENT ON COLUMN prepaid_bucket.rating_type IS 'Constant prepaid, enforcing the owning account mode.';
        COMMENT ON COLUMN prepaid_bucket.created_at IS 'Creation instant from the injected clock.';

        CREATE FUNCTION prepaid_bucket_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'prepaid bucket identities must be retained'
                USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER prepaid_bucket_immutable BEFORE UPDATE OR DELETE ON prepaid_bucket
            FOR EACH ROW EXECUTE FUNCTION prepaid_bucket_immutable();

        CREATE FUNCTION billing_account_mode_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.rating_type IS DISTINCT FROM OLD.rating_type AND (
                EXISTS (SELECT 1 FROM product_order WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM product WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM applied_customer_billing_rate WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM customer_bill WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM customer_bill_on_demand WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM payment WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM refund WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM bill_allocation WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM billing_run_account WHERE billing_account_id = OLD.id) OR
                EXISTS (SELECT 1 FROM prepaid_bucket WHERE billing_account_id = OLD.id)
            ) THEN
                RAISE EXCEPTION 'account mode cannot change after history'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER billing_account_mode_history BEFORE UPDATE OF rating_type ON billing_account
            FOR EACH ROW EXECUTE FUNCTION billing_account_mode_history();
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM billing_account WHERE rating_type <> 'postpaid')
                OR EXISTS (SELECT 1 FROM prepaid_bucket) THEN
                RAISE EXCEPTION 'prepaid account/bucket history must be retained at revision 0034';
            END IF;
        END $$;
        DROP TRIGGER billing_account_mode_history ON billing_account;
        DROP FUNCTION billing_account_mode_history();
        DROP TABLE prepaid_bucket;
        DROP FUNCTION prepaid_bucket_immutable();
        ALTER TABLE billing_account
            DROP CONSTRAINT billing_account_mode_identity,
            DROP CONSTRAINT billing_account_prepaid_credit_limit,
            DROP CONSTRAINT billing_account_rating_type,
            DROP COLUMN rating_type;
    """)
