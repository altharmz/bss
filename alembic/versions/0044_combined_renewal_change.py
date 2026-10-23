"""Let one modify item change a prepaid product's offering and number at its renewal."""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = depends_on = None


def upgrade():
    op.execute("""ALTER TABLE product_order_item
        DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
        ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
            action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND (
                (product_status IS NOT NULL AND product_offering_id IS NULL
                    AND NOT coalesce(details->'product' ? 'realizingResource', false))
                OR (product_status IS NULL AND (product_offering_id IS NOT NULL
                    OR coalesce(details->'product' ? 'realizingResource', false))))));""")
    op.execute(
        "COMMENT ON CONSTRAINT product_order_item_modify_has_product_and_offering"
        " ON product_order_item IS 'A modify item changes the status alone, or the offering,"
        " the number or (a prepaid renewal) both.'"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM product_order_item WHERE action = 'modify'
                AND state <> 'rejected' AND product_offering_id IS NOT NULL
                AND details->'product' ? 'realizingResource') THEN
            RAISE EXCEPTION 'combined offering and number changes must be retained at revision 0044';
        END IF;
    END $$;
    ALTER TABLE product_order_item
        DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
        ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
            action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND
                ((product_offering_id IS NOT NULL)::int + (product_status IS NOT NULL)::int
                + coalesce((details->'product' ? 'realizingResource')::int, 0)) = 1));""")
