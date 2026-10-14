"""Persist the monthly price quoted by a completed add order.

Existing orders have no quote: do not invent history from today's catalog. The
snapshot is written in the order transaction and survives catalog name changes.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE product_order_item
          ADD COLUMN quoted_price_id uuid REFERENCES product_offering_price(id),
          ADD COLUMN quoted_price_name text,
          ADD COLUMN quoted_amount numeric(12,2),
          ADD COLUMN quoted_currency text,
          ADD CONSTRAINT product_order_item_quote CHECK (
            (quoted_price_id IS NULL AND quoted_price_name IS NULL
             AND quoted_amount IS NULL AND quoted_currency IS NULL)
            OR (action = 'add' AND quoted_price_id IS NOT NULL
                AND quoted_price_name IS NOT NULL AND quoted_amount IS NOT NULL
                AND quoted_amount >= 0 AND quoted_currency IS NOT NULL
                AND quoted_currency ~ '^[A-Z]{3}$'))
    """)
    for column, meaning in {
        "quoted_price_id": "Monthly catalog price selected at the order's requested start; NULL for legacy or rejected orders.",
        "quoted_price_name": "Price name at ordering time; immutable quote, not today's catalog name.",
        "quoted_amount": "Quoted full monthly fee (not the prorated first charge), exact cents.",
        "quoted_currency": "ISO currency of the monthly quote, the order account's currency.",
    }.items():
        op.execute(f"COMMENT ON COLUMN product_order_item.{column} IS '{meaning.replace(chr(39), chr(39) * 2)}'")


def downgrade() -> None:
    op.execute("""
        ALTER TABLE product_order_item
          DROP CONSTRAINT product_order_item_quote,
          DROP COLUMN quoted_currency, DROP COLUMN quoted_amount,
          DROP COLUMN quoted_price_name, DROP COLUMN quoted_price_id
    """)
