"""Retained source-pinned funding holds; no public financial path enabled."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE funding_hold (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            billing_account_id uuid NOT NULL,
            currency text NOT NULL,
            amount numeric(12,2) NOT NULL CHECK (amount >= 0 AND amount < 'Infinity'::numeric),
            held_at timestamptz NOT NULL,
            released_at timestamptz CHECK (released_at >= held_at),
            UNIQUE (id, billing_account_id),
            FOREIGN KEY (billing_account_id, currency) REFERENCES billing_account(id, currency)
        );
        CREATE INDEX ON funding_hold(billing_account_id);
        CREATE TABLE funding_hold_source (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            hold_id uuid NOT NULL,
            billing_account_id uuid NOT NULL,
            payment_id uuid,
            credit_bill_id uuid,
            amount numeric(12,2) NOT NULL CHECK (amount > 0 AND amount < 'Infinity'::numeric),
            FOREIGN KEY (hold_id, billing_account_id) REFERENCES funding_hold(id, billing_account_id),
            FOREIGN KEY (payment_id, billing_account_id) REFERENCES payment(id, billing_account_id),
            FOREIGN KEY (credit_bill_id, billing_account_id) REFERENCES customer_bill(id, billing_account_id),
            CHECK (num_nonnulls(payment_id, credit_bill_id) = 1)
        );
        CREATE UNIQUE INDEX ON funding_hold_source(hold_id, payment_id) WHERE payment_id IS NOT NULL;
        CREATE UNIQUE INDEX ON funding_hold_source(hold_id, credit_bill_id) WHERE credit_bill_id IS NOT NULL;
        CREATE INDEX ON funding_hold_source(payment_id);
        CREATE INDEX ON funding_hold_source(credit_bill_id);

        CREATE FUNCTION funding_hold_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'funding_hold' THEN
                IF OLD.released_at IS NULL AND NEW.released_at IS NOT NULL
                    AND (to_jsonb(OLD) - 'released_at') = (to_jsonb(NEW) - 'released_at') THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'funding hold history is immutable' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER funding_hold_retained BEFORE UPDATE OR DELETE ON funding_hold
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();
        CREATE TRIGGER funding_hold_retained BEFORE UPDATE OR DELETE ON funding_hold_source
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();

        CREATE FUNCTION funding_hold_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT FROM funding_hold h WHERE h.billing_account_id = NEW.billing_account_id
                AND h.amount <> coalesce((SELECT sum(s.amount) FROM funding_hold_source s WHERE s.hold_id = h.id), 0)
            ) OR EXISTS (
                SELECT FROM payment p WHERE p.billing_account_id = NEW.billing_account_id
                AND coalesce((SELECT sum(amount) FROM bill_allocation WHERE payment_id = p.id), 0)
                  + coalesce((SELECT sum(amount) FROM refund_source WHERE payment_id = p.id), 0)
                  + coalesce((SELECT sum(s.amount) FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id
                              WHERE s.payment_id = p.id AND h.released_at IS NULL), 0) > p.amount
            ) OR EXISTS (
                SELECT FROM customer_bill b WHERE b.billing_account_id = NEW.billing_account_id
                AND EXISTS (SELECT FROM funding_hold_source WHERE credit_bill_id = b.id)
                AND (b.amount_due >= 0 OR b.state <> 'settled' OR
                     coalesce((SELECT sum(amount) FROM bill_allocation WHERE credit_bill_id = b.id), 0)
                   + coalesce((SELECT sum(amount) FROM refund_source WHERE credit_bill_id = b.id), 0)
                   + coalesce((SELECT sum(s.amount) FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id
                               WHERE s.credit_bill_id = b.id AND h.released_at IS NULL), 0) > -b.amount_due)
            ) THEN
                RAISE EXCEPTION 'funding holds or combined credit usage exceed bounds' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;
    """)
    for table in ("funding_hold", "funding_hold_source"):
        op.execute(f"CREATE TRIGGER credit_account_lock BEFORE INSERT OR UPDATE ON {table}"
                   " FOR EACH ROW EXECUTE FUNCTION credit_account_lock()")
    for table in ("payment", "customer_bill"):
        op.execute(f"CREATE TRIGGER funding_source_account_lock BEFORE UPDATE ON {table}"
                   " FOR EACH ROW EXECUTE FUNCTION credit_account_lock()")
    for table in ("funding_hold", "funding_hold_source", "bill_allocation", "refund_source", "payment", "customer_bill"):
        op.execute(f"CREATE CONSTRAINT TRIGGER funding_hold_bounds AFTER INSERT OR UPDATE ON {table}"
                   " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION funding_hold_bounds()")
    op.execute("""
        CREATE FUNCTION funding_hold_mode_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.rating_type IS DISTINCT FROM OLD.rating_type
                AND EXISTS (SELECT FROM funding_hold WHERE billing_account_id = OLD.id) THEN
                RAISE EXCEPTION 'account mode cannot change after funding holds' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER funding_hold_mode_history BEFORE UPDATE OF rating_type ON billing_account
            FOR EACH ROW EXECUTE FUNCTION funding_hold_mode_history();
    """)
    for table, columns in {
        "funding_hold": {
            "id": "Retained internal reservation identity.",
            "billing_account_id": "Account serialized by its row lock.",
            "currency": "Account currency enforced by composite foreign key.",
            "amount": "Total exact source amounts held, including zero-price reservations.",
            "held_at": "Injected-clock reservation instant.",
            "released_at": "Injected-clock release instant; null while outstanding. Release is irreversible.",
        },
        "funding_hold_source": {
            "id": "Immutable source entry identity.",
            "hold_id": "Retained reservation owning this immutable source amount.",
            "billing_account_id": "Account shared by reservation and source.",
            "payment_id": "Payment source, exclusive with credit_bill_id.",
            "credit_bill_id": "Settled negative bill source, exclusive with payment_id.",
            "amount": "Exact positive amount pinned to the source.",
        },
    }.items():
        op.execute(f"COMMENT ON TABLE {table} IS 'Internal source-pinned funding hold foundation; no public API.'")
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment}'")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM funding_hold) THEN
                RAISE EXCEPTION 'funding hold history must be retained at revision 0035';
            END IF;
        END $$;
        DROP TRIGGER funding_hold_mode_history ON billing_account;
        DROP FUNCTION funding_hold_mode_history();
    """)
    for table in ("bill_allocation", "refund_source", "payment", "customer_bill"):
        op.execute(f"DROP TRIGGER funding_hold_bounds ON {table}")
    for table in ("payment", "customer_bill"):
        op.execute(f"DROP TRIGGER funding_source_account_lock ON {table}")
    op.execute("""
        DROP TABLE funding_hold_source;
        DROP TABLE funding_hold;
        DROP FUNCTION funding_hold_bounds();
        DROP FUNCTION funding_hold_retained();
    """)
