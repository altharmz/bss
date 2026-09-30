"""columns and one table the TMF API needs that 0001 has no place for

- product_order_error_message: a rejected order is stored with its reasons (TMF622
  productOrderErrorMessage).
- customer.name: mandatory on create in TMF629.
- product_offering_price.product_offering_id becomes nullable: in TMF620 a price is created
  on its own and attached by the offering (productOfferingPrice[]). NULL never conflicts
  in the exclusion constraint, so an unattached price cannot overlap anything.
- product_offering_price.lifecycle_status: mandatory on create in TMF620.
- payment.payment_method: mandatory in TMF676 requests and responses.

The new NOT NULL columns have no default and no backfill on purpose: no API existed to
write these tables, so they must be empty. If one is not, ADD COLUMN ... NOT NULL fails
and the deploy stops before the app starts - decide the backfill by hand then.

Written by hand - do not regenerate.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product_order_error_message (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            product_order_id uuid NOT NULL REFERENCES product_order,
            -- NULL = the error concerns the order as a whole
            product_order_item_id uuid REFERENCES product_order_item,
            code text NOT NULL,
            reason text NOT NULL,
            message text,
            occurred_at timestamptz NOT NULL
        )"""
    )
    op.execute("CREATE INDEX ON product_order_error_message (product_order_id)")
    op.execute(
        "CREATE TRIGGER product_order_error_message_set_updated_at"
        " BEFORE UPDATE ON product_order_error_message"
        " FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute("ALTER TABLE customer ADD COLUMN name text NOT NULL")
    op.execute("ALTER TABLE product_offering_price ALTER COLUMN product_offering_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE product_offering_price ADD COLUMN lifecycle_status lifecycle_status NOT NULL"
    )
    op.execute("CREATE INDEX ON product_offering_price (product_offering_id)")
    op.execute("ALTER TABLE payment ADD COLUMN payment_method text NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE payment DROP COLUMN payment_method")
    op.execute("DROP INDEX product_offering_price_product_offering_id_idx")
    op.execute("ALTER TABLE product_offering_price DROP COLUMN lifecycle_status")
    # Fails if an unattached price exists - that row cannot be represented in 0001.
    op.execute("ALTER TABLE product_offering_price ALTER COLUMN product_offering_id SET NOT NULL")
    op.execute("ALTER TABLE customer DROP COLUMN name")
    op.execute("DROP TABLE product_order_error_message")
