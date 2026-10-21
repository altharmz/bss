"""Retained command hold conversion to exact bill allocations."""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE funding_hold_conversion (
            hold_id uuid PRIMARY KEY REFERENCES funding_command_hold(hold_id),
            billing_account_id uuid NOT NULL,
            customer_bill_id uuid NOT NULL UNIQUE,
            converted_at timestamptz NOT NULL,
            UNIQUE (hold_id, billing_account_id),
            FOREIGN KEY (hold_id, billing_account_id)
                REFERENCES funding_hold(id, billing_account_id),
            FOREIGN KEY (customer_bill_id, billing_account_id)
                REFERENCES customer_bill(id, billing_account_id)
        );
        CREATE TABLE funding_conversion_source (
            hold_source_id uuid PRIMARY KEY REFERENCES funding_hold_source(id),
            hold_id uuid NOT NULL REFERENCES funding_hold_conversion(hold_id),
            allocation_id uuid NOT NULL UNIQUE REFERENCES bill_allocation(id),
            billing_account_id uuid NOT NULL,
            FOREIGN KEY (hold_id, billing_account_id)
                REFERENCES funding_hold_conversion(hold_id, billing_account_id)
        );
        CREATE INDEX ON funding_conversion_source(hold_id);
        CREATE INDEX ON funding_hold_conversion(billing_account_id);
        CREATE TRIGGER funding_conversion_retained BEFORE UPDATE OR DELETE ON funding_hold_conversion
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();
        CREATE TRIGGER funding_conversion_retained BEFORE UPDATE OR DELETE ON funding_conversion_source
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();
        CREATE TRIGGER credit_account_lock BEFORE INSERT ON funding_hold_conversion
            FOR EACH ROW EXECUTE FUNCTION credit_account_lock();
        CREATE TRIGGER credit_account_lock BEFORE INSERT ON funding_conversion_source
            FOR EACH ROW EXECUTE FUNCTION credit_account_lock();

        CREATE FUNCTION funding_conversion_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT FROM funding_hold_conversion c
                JOIN funding_hold h ON h.id = c.hold_id
                JOIN customer_bill b ON b.id = c.customer_bill_id
                WHERE c.billing_account_id = NEW.billing_account_id AND (
                   h.released_at IS DISTINCT FROM c.converted_at
                   OR c.converted_at < h.held_at
                   OR b.amount_due <> h.amount
                   OR b.state <> 'settled'
                   OR EXISTS (
                       SELECT FROM funding_hold_source s
                       LEFT JOIN funding_conversion_source m ON m.hold_source_id = s.id
                       WHERE s.hold_id = h.id AND (m.hold_id IS DISTINCT FROM h.id)
                   )
                   OR EXISTS (
                       SELECT FROM funding_conversion_source m
                       JOIN funding_hold_source s ON s.id = m.hold_source_id
                       JOIN bill_allocation a ON a.id = m.allocation_id
                       WHERE m.hold_id = h.id AND (
                           s.hold_id <> h.id OR a.customer_bill_id <> b.id
                           OR a.amount <> s.amount
                           OR a.payment_id IS DISTINCT FROM s.payment_id
                           OR a.credit_bill_id IS DISTINCT FROM s.credit_bill_id
                           OR a.allocated_at <> c.converted_at
                       )
                   )
                   OR (SELECT count(*) FROM bill_allocation WHERE customer_bill_id = b.id)
                      <> (SELECT count(*) FROM funding_conversion_source WHERE hold_id = h.id)
                )
            ) THEN
                RAISE EXCEPTION 'funding conversion must exactly allocate its released hold to a settled bill'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;
    """)
    for table in ("funding_hold_conversion", "funding_conversion_source", "funding_hold", "bill_allocation", "customer_bill"):
        op.execute(f"CREATE CONSTRAINT TRIGGER funding_conversion_bounds AFTER INSERT OR UPDATE ON {table}"
                   " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION funding_conversion_bounds()")
    for table, columns in {
        "funding_hold_conversion": {
            "hold_id": "Command-owned hold consumed exactly once; retained retry result.",
            "billing_account_id": "Account shared by hold and target bill.",
            "customer_bill_id": "One fully funded settled bill with exactly the held total.",
            "converted_at": "Injected conversion instant, also the hold release and allocation instant.",
        },
        "funding_conversion_source": {
            "hold_source_id": "Immutable held source converted exactly once.",
            "hold_id": "Conversion owning this exact source allocation.",
            "allocation_id": "Retained allocation matching the held source and amount exactly.",
            "billing_account_id": "Owning account serialized by its row lock.",
        },
    }.items():
        op.execute(f"COMMENT ON TABLE {table} IS 'Retained internal command hold conversion; no runtime caller.'")
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment}'")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM funding_hold_conversion) THEN
                RAISE EXCEPTION 'funding conversion history must be retained at revision 0037';
            END IF;
        END $$;
    """)
    for table in ("funding_hold", "bill_allocation", "customer_bill"):
        op.execute(f"DROP TRIGGER funding_conversion_bounds ON {table}")
    op.execute("""
        DROP TABLE funding_conversion_source;
        DROP TABLE funding_hold_conversion;
        DROP FUNCTION funding_conversion_bounds();
    """)
