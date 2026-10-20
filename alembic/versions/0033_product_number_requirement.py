"""Snapshot the built-in number requirement at product creation.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE product_order_item
        DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
        ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
            action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND
                ((product_offering_id IS NOT NULL)::int + (product_status IS NOT NULL)::int
                + coalesce((details->'product' ? 'realizingResource')::int, 0)) = 1));""")
    op.execute("ALTER TABLE product ADD COLUMN requires_msisdn boolean NOT NULL DEFAULT false")
    op.execute("COMMENT ON COLUMN product.requires_msisdn IS 'Built-in msisdn requirement at ordering; existing products remain unchecked.'")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM product WHERE requires_msisdn)
            OR EXISTS (SELECT 1 FROM product_order_item
                WHERE action = 'modify' AND details->'product' ? 'realizingResource') THEN
            RAISE EXCEPTION 'number-requiring products must be retained at revision 0033';
        END IF;
    END $$;
    ALTER TABLE product DROP COLUMN requires_msisdn;
    ALTER TABLE product_order_item
        DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
        ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
            action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL
                AND (product_offering_id IS NULL) <> (product_status IS NULL)));""")
