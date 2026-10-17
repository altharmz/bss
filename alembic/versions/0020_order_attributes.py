"""TMF622 productOrder descriptive attributes, the ones PATCH may change.

Orders are processed synchronously, so state, items, dates and the billing account are
settled by the time anyone could PATCH; what remains changeable describes the order:
description, category, priority (TMF's "0" highest .. "4" lowest) and externalId[]
(identifiers of the order in other systems). POST accepts them too. Billing never reads
them.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE product_order
            ADD COLUMN description text CHECK (length(description) > 0),
            ADD COLUMN category text CHECK (length(category) > 0),
            ADD COLUMN priority text CHECK (priority IN ('0', '1', '2', '3', '4')),
            ADD COLUMN external_id jsonb NOT NULL DEFAULT '[]'
                CHECK (jsonb_typeof(external_id) = 'array');
        COMMENT ON COLUMN product_order.description IS 'TMF622 description; optional.';
        COMMENT ON COLUMN product_order.category IS
            'TMF622 category, e.g. residential or enterprise; optional, free text.';
        COMMENT ON COLUMN product_order.priority IS
            'TMF622 priority: 0 (highest) to 4 (lowest); optional.';
        COMMENT ON COLUMN product_order.external_id IS
            'TMF622 externalId[]: the order''s identifiers in other systems (id, owner, type).';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE product_order
            DROP COLUMN external_id,
            DROP COLUMN priority,
            DROP COLUMN category,
            DROP COLUMN description;
    """)
