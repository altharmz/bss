"""The last TMF gaps (gap-analysis "The last gaps").

- `product.name`: the product's own name (TMF637), the offering's name at creation by
  default - back-filled so, and defaulted by `product_default_name` for an insert without
  one. Until now the name answered was the offering's of the day.
- `product.details`: TMF637 descriptive attributes (characteristics, relationships,
  terms, place, bundle members, ...) as sent, validated against the spec.
- `product_status_change`: a product's status from `change_date` on - suspended or
  active again - set by a TMF622 modify item with `product.status`
  (docs/decisions/tmf-api.md#suspension-2026-10-19). Billing does not read it.
- `product_order_item.product_status`: such an item's product.status; a modify item
  carries either an offering or a status.
- `payment.details`, `refund.details`, `customer_bill_on_demand.details`: TMF676 and
  TMF678 descriptive attributes as sent (name, channel, payer, the paymentMethod as
  sent, ...).
- `price_type` gains `discount`, `allowance` and `penalty`: records, never billed
  (docs/decisions/catalog.md#alteration-price-types-2026-10-19).

Rolling back refuses while a status item (a status change's, or a rejected one) or a
price of a new type exists.

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

DETAILS = """ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object')"""


def upgrade() -> None:
    op.execute("ALTER TYPE price_type ADD VALUE 'discount'")
    op.execute("ALTER TYPE price_type ADD VALUE 'allowance'")
    op.execute("ALTER TYPE price_type ADD VALUE 'penalty'")
    op.execute(f"""
        COMMENT ON COLUMN product_offering_price.price_type IS
            'TMF620 priceType: recurring, oneTime, or an alteration (discount, allowance,'
            ' penalty: records). Billing uses recurring only.';
        ALTER TABLE product ADD COLUMN name text, {DETAILS};
        UPDATE product p SET name = o.name
          FROM product_offering o WHERE o.id = p.product_offering_id;
        ALTER TABLE product ALTER COLUMN name SET NOT NULL,
            ADD CONSTRAINT product_name_not_blank CHECK (name <> '');
        COMMENT ON COLUMN product.name IS
            'TMF637 name: the product''s own; by default its offering''s name at creation.';
        COMMENT ON COLUMN product.details IS
            'TMF637 descriptive attributes as sent, validated against the spec; billing'
            ' never reads them.';
        CREATE FUNCTION product_default_name() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.name IS NULL THEN
                SELECT o.name INTO NEW.name FROM product_offering o
                 WHERE o.id = NEW.product_offering_id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER product_default_name BEFORE INSERT ON product
            FOR EACH ROW EXECUTE FUNCTION product_default_name();

        CREATE TABLE product_status_change (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            product_id uuid NOT NULL REFERENCES product,
            change_date date NOT NULL,
            status product_status_type NOT NULL CHECK (status IN ('suspended', 'active')),
            changed_by_item_id uuid NOT NULL UNIQUE REFERENCES product_order_item,
            applied_at timestamptz,
            UNIQUE (product_id, change_date)
        );
        CREATE UNIQUE INDEX product_status_change_one_pending
            ON product_status_change (product_id) WHERE applied_at IS NULL;
        CREATE TRIGGER product_status_change_set_updated_at
            BEFORE UPDATE ON product_status_change
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        COMMENT ON TABLE product_status_change IS
            'TMF622 modify items with product.status: the product is suspended (or active'
            ' again) from change_date on. Billing does not read it.';
        COMMENT ON COLUMN product_status_change.applied_at IS
            'When product.status was set to status (by the order, for a change today; else'
            ' by the run of change_date); NULL until then. At most one pending per product.';
        COMMENT ON COLUMN product_status_change.changed_by_item_id IS
            'The modify item that set the change; a cancelled order deletes it.';

        ALTER TABLE product_order_item
            ADD COLUMN product_status product_status_type
                CHECK (product_status IN ('suspended', 'active')),
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected'
                OR (product_id IS NOT NULL
                    AND (product_offering_id IS NULL) <> (product_status IS NULL))),
            ADD CONSTRAINT product_order_item_status_is_modify CHECK (
                product_status IS NULL OR action = 'modify');
        COMMENT ON COLUMN product_order_item.product_status IS
            'A modify item''s product.status: suspended or active (it carries no offering).';
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add, modify (an offering or a status change), delete and noChange.';

        ALTER TABLE payment {DETAILS};
        COMMENT ON COLUMN payment.details IS
            'TMF676 descriptive attributes as sent (name, channel, payer, the paymentMethod'
            ' as sent, ...), validated against the spec.';
        ALTER TABLE refund {DETAILS};
        COMMENT ON COLUMN refund.details IS
            'TMF676 descriptive attributes as sent (name, channel, requestor, the'
            ' paymentMethod as sent, ...), validated against the spec.';
        ALTER TABLE customer_bill_on_demand {DETAILS};
        COMMENT ON COLUMN customer_bill_on_demand.details IS
            'TMF678 descriptive attributes as sent (relatedParty), validated against the'
            ' spec.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM product_order_item WHERE product_status IS NOT NULL) THEN
                RAISE EXCEPTION 'status items exist (suspensions, resumptions): 0027 has no'
                    ' place for them; delete their orders first';
            END IF;
            IF EXISTS (SELECT 1 FROM product_offering_price
                        WHERE price_type NOT IN ('recurring', 'oneTime')) THEN
                RAISE EXCEPTION 'prices of type discount, allowance or penalty exist';
            END IF;
        END $$;
        ALTER TABLE customer_bill_on_demand DROP COLUMN details;
        ALTER TABLE refund DROP COLUMN details;
        ALTER TABLE payment DROP COLUMN details;
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_status_is_modify,
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            DROP COLUMN product_status,
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected'
                OR (product_id IS NOT NULL AND product_offering_id IS NOT NULL));
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add, modify (an offering change), delete and noChange.';
        DROP TABLE product_status_change;
        DROP TRIGGER product_default_name ON product;
        DROP FUNCTION product_default_name();
        ALTER TABLE product DROP COLUMN details, DROP COLUMN name;

        -- an enum value cannot be dropped: the type is recreated, and with it the
        -- constraints that compare the column
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_no_overlap,
            DROP CONSTRAINT product_offering_price_recurring_has_period;
        ALTER TYPE price_type RENAME TO price_type_0028;
        CREATE TYPE price_type AS ENUM ('recurring', 'oneTime');
        ALTER TABLE product_offering_price
            ALTER COLUMN price_type TYPE price_type USING price_type::text::price_type,
            ADD CONSTRAINT product_offering_price_recurring_has_period
                CHECK ((price_type = 'recurring') = (recurring_period IS NOT NULL)),
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                valid_for WITH &&);
        DROP TYPE price_type_0028;
        COMMENT ON COLUMN product_offering_price.price_type IS
            'TMF620 priceType: recurring or oneTime. Billing uses recurring only.';
    """)
