"""Split one retained command hold across independently settled product bills."""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE funding_hold_conversion ALTER COLUMN customer_bill_id DROP NOT NULL;
        ALTER TABLE funding_conversion_source DROP CONSTRAINT funding_conversion_source_pkey;
        ALTER TABLE funding_conversion_source ADD PRIMARY KEY (hold_source_id, allocation_id);
        CREATE TABLE funding_conversion_bill (
            customer_bill_id uuid PRIMARY KEY,
            hold_id uuid NOT NULL,
            billing_account_id uuid NOT NULL,
            FOREIGN KEY (hold_id, billing_account_id)
                REFERENCES funding_hold_conversion(hold_id, billing_account_id),
            FOREIGN KEY (customer_bill_id, billing_account_id)
                REFERENCES customer_bill(id, billing_account_id)
        );
        CREATE INDEX ON funding_conversion_bill(hold_id);
        CREATE TRIGGER funding_conversion_retained BEFORE UPDATE OR DELETE ON funding_conversion_bill
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();
        CREATE TRIGGER credit_account_lock BEFORE INSERT ON funding_conversion_bill
            FOR EACH ROW EXECUTE FUNCTION credit_account_lock();
        CREATE FUNCTION funding_batch_conversion_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT FROM funding_conversion_bill t
                JOIN funding_hold_conversion c ON c.hold_id = t.hold_id
                WHERE t.billing_account_id = NEW.billing_account_id
                  AND c.customer_bill_id IS NOT NULL
            ) OR EXISTS (
                SELECT FROM funding_hold_conversion c
                JOIN funding_hold h ON h.id = c.hold_id
                WHERE c.billing_account_id = NEW.billing_account_id
                  AND c.customer_bill_id IS NULL AND (
                    h.released_at IS DISTINCT FROM c.converted_at
                    OR c.converted_at < h.held_at
                    OR NOT EXISTS (SELECT FROM funding_conversion_bill WHERE hold_id = h.id)
                    OR h.amount <> (
                        SELECT coalesce(sum(b.amount_due), 0) FROM funding_conversion_bill t
                        JOIN customer_bill b ON b.id = t.customer_bill_id WHERE t.hold_id = h.id
                    )
                    OR EXISTS (
                        SELECT FROM funding_conversion_bill t
                        JOIN customer_bill b ON b.id = t.customer_bill_id
                        WHERE t.hold_id = h.id AND (
                            b.state <> 'settled' OR b.amount_due < 0
                            OR EXISTS (SELECT FROM funding_hold_conversion x
                                       WHERE x.customer_bill_id = b.id)
                            OR b.amount_due <> (SELECT coalesce(sum(a.amount), 0)
                                FROM bill_allocation a WHERE a.customer_bill_id = b.id)
                            OR EXISTS (
                                SELECT FROM bill_allocation a
                                LEFT JOIN funding_conversion_source m ON m.allocation_id = a.id
                                WHERE a.customer_bill_id = b.id
                                  AND m.hold_id IS DISTINCT FROM h.id
                            )
                        )
                    )
                    OR EXISTS (
                        SELECT FROM funding_hold_source s WHERE s.hold_id = h.id
                        AND s.amount <> (SELECT coalesce(sum(a.amount), 0)
                            FROM funding_conversion_source m
                            JOIN bill_allocation a ON a.id = m.allocation_id
                            WHERE m.hold_source_id = s.id AND m.hold_id = h.id)
                    )
                    OR EXISTS (
                        SELECT FROM funding_conversion_source m
                        JOIN funding_hold_source s ON s.id = m.hold_source_id
                        JOIN bill_allocation a ON a.id = m.allocation_id
                        LEFT JOIN funding_conversion_bill t
                          ON t.customer_bill_id = a.customer_bill_id AND t.hold_id = h.id
                        WHERE m.hold_id = h.id AND (
                            s.hold_id <> h.id OR t.customer_bill_id IS NULL
                            OR a.payment_id IS DISTINCT FROM s.payment_id
                            OR a.credit_bill_id IS DISTINCT FROM s.credit_bill_id
                            OR a.allocated_at <> c.converted_at
                        )
                    )
                )
            ) THEN
                RAISE EXCEPTION 'batch funding conversion must exactly allocate its released hold to settled bills'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;
        COMMENT ON TABLE funding_conversion_bill IS
            'Immutable bill set consuming one command hold atomically; legacy single-bill conversions retain their original target.';
        COMMENT ON COLUMN funding_conversion_bill.customer_bill_id IS 'Independently settled purchase bill, owned by exactly one conversion.';
        COMMENT ON COLUMN funding_conversion_bill.hold_id IS 'Command hold whose exact sources fund this bill.';
        COMMENT ON COLUMN funding_conversion_bill.billing_account_id IS 'Shared owner of hold and bill, serialized by account lock.';
        COMMENT ON COLUMN funding_hold_conversion.customer_bill_id IS
            'Legacy single-bill target; null identifies a retained multi-bill conversion.';
        COMMENT ON COLUMN funding_conversion_source.hold_source_id IS
            'Immutable held source; allocations across bills must sum to its exact amount.';
    """)
    for table in (
        "funding_conversion_bill", "funding_hold_conversion", "funding_conversion_source",
        "funding_hold", "bill_allocation", "customer_bill",
    ):
        op.execute(f"CREATE CONSTRAINT TRIGGER funding_batch_conversion_bounds AFTER INSERT OR UPDATE ON {table}"
                   " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION funding_batch_conversion_bounds()")


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM funding_hold_conversion WHERE customer_bill_id IS NULL) THEN
                RAISE EXCEPTION 'multi-bill conversion history must be retained at revision 0041';
            END IF;
        END $$;
    """)
    for table in (
        "funding_conversion_bill", "funding_hold_conversion", "funding_conversion_source",
        "funding_hold", "bill_allocation", "customer_bill",
    ):
        op.execute(f"DROP TRIGGER funding_batch_conversion_bounds ON {table}")
    op.execute("""
        DROP FUNCTION funding_batch_conversion_bounds();
        DROP TABLE funding_conversion_bill;
        ALTER TABLE funding_hold_conversion ALTER COLUMN customer_bill_id SET NOT NULL;
        ALTER TABLE funding_conversion_source DROP CONSTRAINT funding_conversion_source_pkey;
        ALTER TABLE funding_conversion_source ADD PRIMARY KEY (hold_source_id);
        COMMENT ON COLUMN funding_hold_conversion.customer_bill_id IS
            'One fully funded settled bill with exactly the held total.';
        COMMENT ON COLUMN funding_conversion_source.hold_source_id IS
            'Immutable held source converted exactly once.';
    """)
