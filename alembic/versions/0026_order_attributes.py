"""TMF622 attributes and draft orders (gap-analysis slice 8a).

- `details` on `product_order` and `product_order_item`: the attributes that are
  documents to us (note, relatedParty, channel, quote, agreement, ...; an item's
  appointment, itemTerm, productOrderItemRelationship, ...) as validated against the
  vendored spec and stored as sent. Processing never reads them.
- `product_order.requested_initial_state`: TMF622 requestedInitialState, `acknowledged`
  (processed at once, as before) or `draft` (stored, processed when PATCHed to
  `acknowledged`). Existing orders were all acknowledged.
- `product_order.billing_account_id` is optional, as in the spec: a draft may not name
  one yet. Every processed order has one (derived from its items if not sent).
- `product_order_item.parent_id`: a nested productOrderItem's parent item, in the same
  order. Existing items are all top-level.
- `product_order_item.state` is NULL while the order is a draft: a draft's items are not
  processed, so they hold no state (and no delete item of a draft counts as open).

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

DETAILS = """ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object')"""


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE product_order {DETAILS},
            ADD COLUMN requested_initial_state text NOT NULL DEFAULT 'acknowledged'
                CHECK (requested_initial_state IN ('acknowledged', 'draft')),
            ALTER COLUMN billing_account_id DROP NOT NULL,
            ADD CONSTRAINT product_order_processed_has_account
                CHECK (billing_account_id IS NOT NULL OR state IN ('draft', 'cancelled'));
        ALTER TABLE product_order_item {DETAILS},
            ALTER COLUMN state DROP NOT NULL,
            ADD COLUMN parent_id uuid,
            ADD CONSTRAINT product_order_item_id_order_key UNIQUE (id, product_order_id),
            ADD CONSTRAINT product_order_item_parent_fkey
                FOREIGN KEY (parent_id, product_order_id)
                REFERENCES product_order_item (id, product_order_id);
        CREATE INDEX ON product_order_item (parent_id);
        COMMENT ON COLUMN product_order.details IS
            'TMF622 descriptive attributes as sent, validated against the spec;'
            ' processing never reads them.';
        COMMENT ON COLUMN product_order_item.details IS
            'TMF622 descriptive item attributes as sent, validated against the spec;'
            ' processing never reads them.';
        COMMENT ON COLUMN product_order.requested_initial_state IS
            'TMF622 requestedInitialState: acknowledged (processed at once) or draft'
            ' (processed when PATCHed to acknowledged).';
        COMMENT ON COLUMN product_order.billing_account_id IS
            'TMF622 billingAccount, sent or derived from the items; NULL only for a draft'
            ' (or a cancelled one) that names none yet.';
        COMMENT ON COLUMN product_order.state IS
            'TMF622 state. Used: (draft ->) acknowledged -> inProgress -> completed, or'
            ' acknowledged -> rejected; completed or draft -> cancelled.';
        COMMENT ON COLUMN product_order_item.parent_id IS
            'TMF622 nested productOrderItem: the item this one is nested in (same order).';
        COMMENT ON COLUMN product_order_item.state IS
            'TMF622 item state: completed, rejected or cancelled once processed; NULL'
            ' while the order is a draft.';
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add, delete and noChange are executed; modify is rejected.';
    """)


def downgrade() -> None:
    # Fails while a draft (or an order without an account) exists: delete it first.
    # Nested items become top-level; their documents are lost.
    op.execute("""
        DROP INDEX product_order_item_parent_id_idx;
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_parent_fkey,
            DROP CONSTRAINT product_order_item_id_order_key,
            DROP COLUMN parent_id,
            ALTER COLUMN state SET NOT NULL,
            DROP COLUMN details;
        ALTER TABLE product_order
            DROP CONSTRAINT product_order_processed_has_account,
            ALTER COLUMN billing_account_id SET NOT NULL,
            DROP COLUMN requested_initial_state,
            DROP COLUMN details;
        COMMENT ON COLUMN product_order.state IS
            'TMF622 state. Used: acknowledged -> inProgress -> completed, or acknowledged'
            ' -> rejected.';
        COMMENT ON COLUMN product_order_item.action IS
            'TMF622 action: add or delete are executed; modify and noChange are rejected.';
        COMMENT ON COLUMN product_order_item.state IS
            'TMF622 item state: completed, rejected or cancelled once processed.';
        COMMENT ON COLUMN product_order.billing_account_id IS NULL;
    """)
