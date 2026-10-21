"""Retain prepaid service grants; posting and entitlement callers follow separately."""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE prepaid_command ADD CONSTRAINT prepaid_command_grant_owner
            UNIQUE (id, billing_account_id, kind);
        ALTER TABLE billing_run ADD CONSTRAINT billing_run_grant_command UNIQUE (id, command_id);
        ALTER TABLE applied_customer_billing_rate ADD CONSTRAINT abr_grant_identity
            UNIQUE (id, product_id, billing_account_id, billing_run_id, period,
                    currency, amount, tax_amount, charge_type);
        CREATE TABLE funded_interval (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id uuid NOT NULL,
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            rating_type text NOT NULL DEFAULT 'prepaid' CHECK (rating_type = 'prepaid'),
            command_id uuid NOT NULL,
            command_kind text NOT NULL CHECK (command_kind IN ('order', 'renewal')),
            billing_run_id uuid NOT NULL,
            applied_rate_id uuid NOT NULL UNIQUE,
            charge_type charge_type NOT NULL DEFAULT 'recurring' CHECK (charge_type = 'recurring'),
            authorized_at timestamptz NOT NULL CHECK (isfinite(authorized_at)),
            period daterange NOT NULL CHECK (
                NOT isempty(period) AND lower(period) IS NOT NULL AND upper(period) IS NOT NULL
                AND isfinite(lower(period)) AND isfinite(upper(period))),
            anchor_date date NOT NULL CHECK (isfinite(anchor_date)),
            interval_index integer NOT NULL CHECK (interval_index >= 0),
            amount numeric(12,2) NOT NULL CHECK (amount >= 0 AND amount <> 'NaN'::numeric),
            tax_amount numeric(12,2) NOT NULL CHECK (tax_amount >= 0 AND tax_amount <> 'NaN'::numeric),
            quote jsonb NOT NULL CHECK (jsonb_typeof(quote) = 'object'),
            CHECK (anchor_date <= lower(period)),
            UNIQUE (command_id, product_id),
            UNIQUE (product_id, anchor_date, interval_index),
            EXCLUDE USING gist (product_id WITH =, period WITH &&),
            FOREIGN KEY (billing_account_id, currency, rating_type)
                REFERENCES billing_account(id, currency, rating_type),
            FOREIGN KEY (product_id, billing_account_id)
                REFERENCES product(id, billing_account_id),
            FOREIGN KEY (command_id, billing_account_id, command_kind)
                REFERENCES prepaid_command(id, billing_account_id, kind),
            FOREIGN KEY (billing_run_id, command_id) REFERENCES billing_run(id, command_id),
            FOREIGN KEY (applied_rate_id, product_id, billing_account_id, billing_run_id,
                         period, currency, amount, tax_amount, charge_type)
                REFERENCES applied_customer_billing_rate
                    (id, product_id, billing_account_id, billing_run_id, period,
                     currency, amount, tax_amount, charge_type)
        );
        CREATE INDEX ON funded_interval(billing_account_id);
        CREATE INDEX ON funded_interval(billing_run_id);
        CREATE FUNCTION funded_interval_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'funded interval history is immutable and must be retained'
                USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER funded_interval_retained BEFORE UPDATE OR DELETE ON funded_interval
            FOR EACH ROW EXECUTE FUNCTION funded_interval_retained();
        COMMENT ON TABLE funded_interval IS
            'Immutable prepaid service grants. Persistence foundation only; the future billing owner must atomically post and settle the charge, convert its hold and complete the command.';
    """)
    for column, comment in {
        "id": "Retained service grant identity.",
        "product_id": "Product receiving the grant; coverage cannot overlap for this product.",
        "billing_account_id": "Shared prepaid owner of product, command and charge.",
        "currency": "Account and charge currency.",
        "rating_type": "Constant prepaid, enforced against the account mode.",
        "command_id": "Idempotent purchase identity; at most one grant per command/product.",
        "command_kind": "Order or renewal; transfers cannot grant service.",
        "billing_run_id": "Retained command run, never a daily run.",
        "applied_rate_id": "Original recurring charge with exactly matching coverage and quote totals.",
        "charge_type": "Constant recurring, enforcing the original charge kind.",
        "authorized_at": "Injected actual authorization instant; service is never backdated to midnight.",
        "period": "Finite nonempty half-open business-date coverage; upper bound is paid-through.",
        "anchor_date": "Original calendar anchor retained across renewals.",
        "interval_index": "Nonnegative interval index under the original anchor.",
        "amount": "Exact original charge before tax; immutable and matched to the ABR.",
        "tax_amount": "Exact original tax total; immutable and matched to the ABR.",
        "quote": "Immutable price/tax snapshot; the future posting caller validates its structure.",
    }.items():
        op.execute(f"COMMENT ON COLUMN funded_interval.{column} IS '{comment}'")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM funded_interval) THEN
                RAISE EXCEPTION 'funded interval history must be retained at revision 0040';
            END IF;
        END $$;
        DROP TABLE funded_interval;
        DROP FUNCTION funded_interval_retained();
        ALTER TABLE applied_customer_billing_rate DROP CONSTRAINT abr_grant_identity;
        ALTER TABLE billing_run DROP CONSTRAINT billing_run_grant_command;
        ALTER TABLE prepaid_command DROP CONSTRAINT prepaid_command_grant_owner;
    """)
