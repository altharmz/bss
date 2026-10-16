"""Recorded payouts consume unused payment/credit-note credit.

Revision ID: 0010; Revises: 0009.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE refund (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            billing_account_id uuid NOT NULL,
            currency char(3) NOT NULL,
            amount numeric(12,2) NOT NULL CHECK (amount > 0),
            refunded_at timestamptz NOT NULL,
            status text NOT NULL DEFAULT 'done' CHECK (status = 'done'),
            method_name text,
            method_type text,
            description text,
            payment_id uuid,
            FOREIGN KEY (billing_account_id, currency) REFERENCES billing_account(id, currency),
            FOREIGN KEY (payment_id, billing_account_id) REFERENCES payment(id, billing_account_id),
            UNIQUE (id, billing_account_id),
            CHECK (method_name IS NOT NULL OR method_type IS NOT NULL)
        );
        CREATE TABLE refund_source (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            refund_id uuid NOT NULL,
            billing_account_id uuid NOT NULL,
            payment_id uuid,
            credit_bill_id uuid,
            amount numeric(12,2) NOT NULL CHECK (amount > 0),
            FOREIGN KEY (refund_id, billing_account_id) REFERENCES refund(id, billing_account_id),
            FOREIGN KEY (payment_id, billing_account_id) REFERENCES payment(id, billing_account_id),
            FOREIGN KEY (credit_bill_id, billing_account_id) REFERENCES customer_bill(id, billing_account_id),
            CHECK (num_nonnulls(payment_id, credit_bill_id) = 1)
        );
        CREATE INDEX ON refund(billing_account_id);
        CREATE INDEX ON refund_source(refund_id);
        CREATE INDEX ON refund_source(payment_id);
        CREATE INDEX ON refund_source(credit_bill_id);
        CREATE FUNCTION credit_account_lock() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM id FROM billing_account WHERE id = NEW.billing_account_id FOR UPDATE;
            RETURN NEW;
        END $$;
        CREATE FUNCTION credit_usage_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT FROM payment p WHERE p.billing_account_id = NEW.billing_account_id
                AND coalesce((SELECT sum(amount) FROM bill_allocation WHERE payment_id = p.id), 0)
                  + coalesce((SELECT sum(amount) FROM refund_source WHERE payment_id = p.id), 0) > p.amount
            ) OR EXISTS (
                SELECT FROM customer_bill b WHERE b.billing_account_id = NEW.billing_account_id
                AND EXISTS (SELECT FROM refund_source WHERE credit_bill_id = b.id)
                AND (b.amount_due >= 0 OR b.state <> 'settled' OR
                     coalesce((SELECT sum(amount) FROM bill_allocation WHERE credit_bill_id = b.id), 0)
                   + coalesce((SELECT sum(amount) FROM refund_source WHERE credit_bill_id = b.id), 0) > -b.amount_due)
            ) OR EXISTS (
                SELECT FROM refund r WHERE r.billing_account_id = NEW.billing_account_id
                AND (r.amount <> coalesce((SELECT sum(amount) FROM refund_source WHERE refund_id = r.id), 0)
                  OR EXISTS (SELECT FROM refund_source s WHERE s.refund_id = r.id
                             AND r.payment_id IS NOT NULL AND s.payment_id IS DISTINCT FROM r.payment_id))
            ) THEN
                RAISE EXCEPTION 'refund sources or credit usage exceed their bounds'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;
    """)
    for table in ("refund", "refund_source", "bill_allocation"):
        op.execute(f"CREATE TRIGGER credit_account_lock BEFORE INSERT ON {table}"
                   " FOR EACH ROW EXECUTE FUNCTION credit_account_lock()")
        op.execute(f"CREATE CONSTRAINT TRIGGER credit_usage_bounds AFTER INSERT ON {table}"
                   " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION credit_usage_bounds()")
    op.execute("""
        CREATE FUNCTION refund_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'recorded refunds are immutable' USING ERRCODE = 'check_violation'; END $$;
    """)
    for table, columns in {
        "refund": {
            "id": "Server-generated refund identifier.", "created_at": "Database insertion time.",
            "billing_account_id": "Account whose credit was paid out.", "currency": "Account currency.",
            "amount": "Positive payout in exact cents.", "refunded_at": "Payout recording time from injected clock.",
            "status": "Completed payout only; done.", "method_name": "Payment method name as supplied.",
            "method_type": "Payment method type as supplied.", "description": "Optional explanation.",
            "payment_id": "Optional payment restricting refundable sources.",
        },
        "refund_source": {
            "id": "Source entry identifier.", "created_at": "Database insertion time.",
            "refund_id": "Recorded payout consuming this credit.", "billing_account_id": "Account shared by payout and source.",
            "payment_id": "Payment credit source; exclusive with credit_bill_id.",
            "credit_bill_id": "Issued negative bill credit source.", "amount": "Positive credit consumed by payout.",
        },
    }.items():
        op.execute(f"COMMENT ON TABLE {table} IS 'TMF676 recorded refund and its consumed credit.'")
        for column, meaning in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{meaning}'")
        op.execute(f"CREATE TRIGGER refund_immutable BEFORE UPDATE OR DELETE ON {table}"
                   " FOR EACH ROW EXECUTE FUNCTION refund_immutable()")


def downgrade() -> None:
    for table in ("bill_allocation", "refund_source", "refund"):
        op.execute(f"DROP TRIGGER credit_usage_bounds ON {table}")
        op.execute(f"DROP TRIGGER credit_account_lock ON {table}")
    op.execute("DROP TABLE refund_source; DROP TABLE refund")
    op.execute("DROP FUNCTION credit_usage_bounds(); DROP FUNCTION credit_account_lock(); DROP FUNCTION refund_immutable()")
