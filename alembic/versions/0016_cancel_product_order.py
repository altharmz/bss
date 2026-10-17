"""TMF622 cancelProductOrder: undoing a completed order before it takes effect.

Orders complete synchronously, so a cancellation undoes a completed order whose effects
are all still in the future: its new products have not started, its terminations have
not happened, and billing has not charged the periods involved. The order and its items
become `cancelled`; `product_order` gets the TMF622 cancellationDate/cancellationReason.
A refused cancellation is a 409 and is not stored, so every stored request is done.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE product_order
            ADD COLUMN cancellation_date timestamptz,
            ADD COLUMN cancellation_reason text;
        -- only after a downgrade and a new upgrade: orders cancelled under an earlier 0016
        -- lost their date with the column; their completion is the best bound left
        UPDATE product_order SET cancellation_date = coalesce(completion_date, order_date)
         WHERE state = 'cancelled';
        ALTER TABLE product_order
            ADD CONSTRAINT product_order_cancelled_has_date
                CHECK ((state = 'cancelled') = (cancellation_date IS NOT NULL)),
            ADD CONSTRAINT product_order_reason_when_cancelled
                CHECK (cancellation_reason IS NULL OR cancellation_date IS NOT NULL),
            ADD CONSTRAINT product_order_cancellation_reason_check
                CHECK (length(cancellation_reason) > 0);
        CREATE TABLE cancel_product_order (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            product_order_id uuid NOT NULL UNIQUE REFERENCES product_order,
            creation_date timestamptz NOT NULL,
            requested_cancellation_date timestamptz,
            cancellation_reason text CHECK (length(cancellation_reason) > 0)
        );
        COMMENT ON TABLE cancel_product_order IS
            'TMF622 cancelProductOrder: done on insert (a refused one is not stored); one per order.';
        COMMENT ON COLUMN product_order.cancellation_date IS
            'TMF622 cancellationDate: when a cancelProductOrder cancelled it; set exactly when cancelled.';
        COMMENT ON COLUMN product_order.cancellation_reason IS
            'TMF622 cancellationReason, from the cancelProductOrder; optional.';
    """)
    for column, meaning in {
        "id": "Server-generated identifier.",
        "created_at": "Database insertion time; stable list ordering.",
        "product_order_id": "The cancelled order; at most one cancellation each.",
        "creation_date": "When it was requested and took effect (application clock).",
        "requested_cancellation_date": "As requested: absent or today (BILLING_TZ); never later.",
        "cancellation_reason": "Optional free text, copied onto the order.",
    }.items():
        op.execute(f"COMMENT ON COLUMN cancel_product_order.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        DROP TABLE cancel_product_order;
        ALTER TABLE product_order
            DROP CONSTRAINT product_order_cancellation_reason_check,
            DROP CONSTRAINT product_order_reason_when_cancelled,
            DROP CONSTRAINT product_order_cancelled_has_date,
            DROP COLUMN cancellation_reason,
            DROP COLUMN cancellation_date;
    """)
