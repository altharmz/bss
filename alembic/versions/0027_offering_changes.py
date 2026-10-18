"""Offering changes: TMF622 modify items (gap-analysis slice 8b).

- `product_offering_change`: a product's offering from `change_date` on, set by a modify
  item (docs/decisions/billing-core.md#offering-changes-2026-10-18). The product keeps
  the offering it was created with; the offering on day d is the latest change on or
  before d, else that one. One change per product and day.
- `applied_customer_billing_rate.product_offering_id`: the offering an ABR bills (a
  refund: its charge's). Back-filled from the product - until now every product has had
  one offering; an insert without one gets the product's offering on the period's first
  day (trigger `abr_default_offering`, via `product_offering_on`).
- `abr_no_overlap` becomes `abr_one_per_start`: one charge and one refund per product and
  first day. A charge is refunded, never shortened, so after a mid-period change the old
  offering's charge overlaps the new one's.

- `product_order_item`: a modify item is quoted like an add item and names its product
  and offering (the modify items stored before were all rejected).

Rolling back refuses while a change exists (its ABRs would no longer match the product's
single offering) or a modify item that was not rejected (cancelled, or a draft's).

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE product_offering_change (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            product_id uuid NOT NULL REFERENCES product,
            change_date date NOT NULL,
            product_offering_id uuid NOT NULL REFERENCES product_offering,
            changed_by_item_id uuid NOT NULL UNIQUE REFERENCES product_order_item,
            effect_announced_at timestamptz,
            UNIQUE (product_id, change_date)
        );
        COMMENT ON COLUMN product_offering_change.effect_announced_at IS
            'When the ProductAttributeValueChangeEvent of the change taking effect was'
            ' emitted (by the order, for a change today; else by the run of change_date);'
            ' NULL until then. Makes a retried run emit it once.';
        CREATE INDEX ON product_offering_change (product_offering_id);
        CREATE TRIGGER product_offering_change_set_updated_at
            BEFORE UPDATE ON product_offering_change
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        COMMENT ON TABLE product_offering_change IS
            'TMF622 modify items: the product''s offering from change_date on (billed'
            ' prorated). The offering on day d is the latest change on or before d, else'
            ' product.product_offering_id.';
        COMMENT ON COLUMN product_offering_change.changed_by_item_id IS
            'The modify item that set the change; a cancelled order deletes it.';
        COMMENT ON COLUMN product.product_offering_id IS
            'The offering the product was created with; later ones in'
            ' product_offering_change.';

        CREATE FUNCTION product_offering_on(product uuid, day date) RETURNS uuid
            LANGUAGE sql STABLE AS $$
            SELECT coalesce(
                (SELECT c.product_offering_id FROM product_offering_change c
                  WHERE c.product_id = product AND c.change_date <= day
                  ORDER BY c.change_date DESC LIMIT 1),
                (SELECT p.product_offering_id FROM product p WHERE p.id = product))
        $$;
        COMMENT ON FUNCTION product_offering_on(uuid, date) IS
            'The offering of a product on a day: the latest change on or before it, else'
            ' the one it was created with.';

        ALTER TABLE applied_customer_billing_rate
            ADD COLUMN product_offering_id uuid REFERENCES product_offering;
        UPDATE applied_customer_billing_rate a SET product_offering_id = p.product_offering_id
            FROM product p WHERE p.id = a.product_id;
        ALTER TABLE applied_customer_billing_rate
            ALTER COLUMN product_offering_id SET NOT NULL;
        CREATE INDEX ON applied_customer_billing_rate (product_offering_id);
        COMMENT ON COLUMN applied_customer_billing_rate.product_offering_id IS
            'The offering the ABR bills (a refund: its charge''s). Billing sets it; an'
            ' insert without one gets the product''s offering on the first day of period.';
        CREATE FUNCTION abr_default_offering() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.product_offering_id IS NULL THEN
                NEW.product_offering_id := product_offering_on(NEW.product_id, lower(NEW.period));
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER abr_default_offering BEFORE INSERT ON applied_customer_billing_rate
            FOR EACH ROW EXECUTE FUNCTION abr_default_offering();

        -- A charge is never shortened, it is refunded: after a mid-period change the old
        -- offering's charge (and its refund) overlaps the new one's. What stays unique is
        -- one charge and one refund per product and first day.
        ALTER TABLE applied_customer_billing_rate DROP CONSTRAINT abr_no_overlap;
        CREATE UNIQUE INDEX abr_one_per_start
            ON applied_customer_billing_rate (product_id, charge_type, lower(period));
        COMMENT ON INDEX abr_one_per_start IS
            'One charge and one refund per product and first day; overlapping ranges are'
            ' allowed (an offering change refunds the old charge and charges the new one).';
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add, modify (an offering change), delete and noChange.';

        -- a modify item is quoted like an add item (its new offering's price), and names
        -- its product and offering; the modify items stored before were all rejected
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_quote,
            ADD CONSTRAINT product_order_item_quote CHECK (
                (quoted_price_id IS NULL AND quoted_price_name IS NULL
                 AND quoted_amount IS NULL AND quoted_currency IS NULL)
                OR (action IN ('add', 'modify') AND quoted_price_id IS NOT NULL
                    AND quoted_price_name IS NOT NULL AND quoted_amount IS NOT NULL
                    AND quoted_amount >= 0 AND quoted_currency IS NOT NULL
                    AND quoted_currency ~ '^[A-Z]{3}$')),
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected'
                OR (product_id IS NOT NULL AND product_offering_id IS NOT NULL));
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM product_offering_change) THEN
                RAISE EXCEPTION 'offering changes exist: their ABRs would no longer match'
                    ' a single offering per product';
            END IF;
            IF EXISTS (SELECT 1 FROM product_order_item
                        WHERE action = 'modify' AND state IS DISTINCT FROM 'rejected') THEN
                RAISE EXCEPTION 'modify items that were not rejected exist (0026 rejects'
                    ' every modify item): delete their orders first';
            END IF;
        END $$;
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            DROP CONSTRAINT product_order_item_quote,
            ADD CONSTRAINT product_order_item_quote CHECK (
                (quoted_price_id IS NULL AND quoted_price_name IS NULL
                 AND quoted_amount IS NULL AND quoted_currency IS NULL)
                OR (action = 'add' AND quoted_price_id IS NOT NULL
                    AND quoted_price_name IS NOT NULL AND quoted_amount IS NOT NULL
                    AND quoted_amount >= 0 AND quoted_currency IS NOT NULL
                    AND quoted_currency ~ '^[A-Z]{3}$'));
        DROP INDEX abr_one_per_start;
        ALTER TABLE applied_customer_billing_rate ADD CONSTRAINT abr_no_overlap
            EXCLUDE USING gist (product_id WITH =, charge_type WITH =, period WITH &&);
        DROP TRIGGER abr_default_offering ON applied_customer_billing_rate;
        DROP FUNCTION abr_default_offering();
        ALTER TABLE applied_customer_billing_rate DROP COLUMN product_offering_id;
        DROP FUNCTION product_offering_on(uuid, date);
        DROP TABLE product_offering_change;
        COMMENT ON COLUMN product.product_offering_id IS NULL;
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add, delete and noChange are executed; modify is rejected.';
    """)
