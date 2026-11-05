"""Network provisioning commands of prepaid order items (issue #183).

Revision ID: 0052
Revises: 0051
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE network_command (
            id uuid PRIMARY KEY,
            product_order_id uuid NOT NULL REFERENCES product_order(id),
            product_order_item_id uuid NOT NULL UNIQUE REFERENCES product_order_item(id),
            product_id uuid REFERENCES product(id),
            kind text NOT NULL CHECK (kind IN
                ('activate', 'suspend', 'restore', 'deactivate', 'change_profile')),
            state text NOT NULL DEFAULT 'pending' CHECK (state IN
                ('pending', 'confirmed', 'refused', 'exhausted', 'cancelled')),
            due_date date NOT NULL,
            profile jsonb NOT NULL CHECK (jsonb_typeof(profile) = 'object'),
            request jsonb CHECK (jsonb_typeof(request) = 'object'),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_error text,
            refusal_code text,
            created_at timestamptz NOT NULL,
            attempted_at timestamptz,
            confirmed_at timestamptz,
            CHECK ((state = 'refused') = (refusal_code IS NOT NULL)),
            CHECK ((state = 'confirmed') = (confirmed_at IS NOT NULL))
        );
        CREATE INDEX network_command_order_idx ON network_command (product_order_id);
        CREATE INDEX network_command_product_idx ON network_command (product_id)
            WHERE product_id IS NOT NULL;
        CREATE INDEX network_command_due_idx ON network_command (due_date, id)
            WHERE state = 'pending';
        COMMENT ON TABLE network_command IS
            'Durable outbound provisioning command of one prepaid order item; sent by the billing worker through the network adapter, the item completes only when it is confirmed.';
        COMMENT ON COLUMN network_command.id IS
            'Idempotency key sent to the network: derived from the order item and the command kind.';
        COMMENT ON COLUMN network_command.kind IS
            'activate (add), suspend, restore (resume), deactivate (terminate), change_profile (offering change).';
        COMMENT ON COLUMN network_command.state IS
            'pending (to send, or retried after a temporary error), confirmed, refused (final: the network said no), exhausted (temporary errors used up the automatic attempts; an operator retries), cancelled (the order was cancelled first).';
        COMMENT ON COLUMN network_command.profile IS
            'The offering''s effective network profile at acceptance, sent unchanged.';
        COMMENT ON COLUMN network_command.request IS
            'The request as first sent (TMF641-shaped, line identifiers resolved); every retry resends it unchanged.';
        COMMENT ON COLUMN network_command.last_error IS
            'The last temporary error, or the network''s reason for a refusal.';
        COMMENT ON COLUMN network_command.refusal_code IS
            'The network''s refusal code; set only when refused.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE network_command")
