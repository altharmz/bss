"""Nothing a TMF client sends is dropped or rewritten (gap-analysis slice 1).

- `base_type`, `schema_location` on every table a client creates rows in: the TMF
  `@baseType` and `@schemaLocation` as sent (Extensible_FVO), returned as stored. The
  billing account is not among them: it carries our own extension's values.
- `payment.payment_method_name` and `payment_method_type`: TMF676's paymentMethod `name`
  and `@type`, each as sent (one of them at least). Existing payments kept one word for
  both; the back-fill repeats it in both, which is what they answered so far.
  `payment_method` stays the one-word label the UI shows.
- `product_order_item.position`: the order in which the client sent the items. Existing
  items are numbered in the order they were returned so far (`item_id` as text).

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

META_TABLES = (
    "party",
    "customer",
    "billing_cycle_specification",
    "bill_format",
    "bill_presentation_media",
    "financial_account",
    "party_account",
    "product_specification",
    "product_offering",
    "product_offering_price",
    "category",
    "product_catalog",
    "catalog_job",
    "product_order",
    "cancel_product_order",
    "product",
    "payment",
    "refund",
    "customer_bill_on_demand",
    "event_subscription",
)


def upgrade() -> None:
    for table in META_TABLES:
        op.execute(f"""
            ALTER TABLE {table}
                ADD COLUMN base_type text CHECK (length(base_type) > 0),
                ADD COLUMN schema_location text CHECK (length(schema_location) > 0);
            COMMENT ON COLUMN {table}.base_type IS
                'TMF @baseType as the client sent it (the super-class when sub-classing).';
            COMMENT ON COLUMN {table}.schema_location IS
                'TMF @schemaLocation as the client sent it (a JSON Schema URI).';
        """)
    op.execute("""
        ALTER TABLE payment
            ADD COLUMN payment_method_name text CHECK (length(payment_method_name) > 0),
            ADD COLUMN payment_method_type text CHECK (length(payment_method_type) > 0);
        UPDATE payment SET payment_method_name = payment_method,
                           payment_method_type = payment_method;
        ALTER TABLE payment ADD CONSTRAINT payment_method_named
            CHECK (payment_method_name IS NOT NULL OR payment_method_type IS NOT NULL);
        COMMENT ON COLUMN payment.payment_method_name IS
            'TMF676 paymentMethod.name as sent; null when only @type was sent.';
        COMMENT ON COLUMN payment.payment_method_type IS
            'TMF676 paymentMethod.@type as sent; null when only name was sent.';
        COMMENT ON COLUMN payment.payment_method IS
            'The one-word label of the method (its name, else its @type) the UI shows.';

        ALTER TABLE product_order_item ADD COLUMN position integer;
        UPDATE product_order_item i SET position = n.position
        FROM (
            SELECT id, row_number() OVER (PARTITION BY product_order_id ORDER BY item_id)
                       AS position
            FROM product_order_item
        ) n
        WHERE n.id = i.id;
        ALTER TABLE product_order_item
            ALTER COLUMN position SET NOT NULL,
            ADD CONSTRAINT product_order_item_position_unique
                UNIQUE (product_order_id, position);
        COMMENT ON COLUMN product_order_item.position IS
            'Where the client put the item in productOrderItem[] (1 = first).';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE product_order_item DROP COLUMN position;
        ALTER TABLE payment
            DROP COLUMN payment_method_type,
            DROP COLUMN payment_method_name;
        COMMENT ON COLUMN payment.payment_method IS
            'TMF676 paymentMethod: the one word stored (its name or @type).';
    """)
    for table in META_TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN schema_location, DROP COLUMN base_type")
