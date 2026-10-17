"""Events and their deliveries: the outbox the hubs are served from.

A write that changes a resource stores its TMF event in the same transaction
(app/events/emit.py), with one delivery per live subscription that wants it - only
then: an event no hub wants is not stored. The worker POSTs pending deliveries to their
callbacks (app/events/deliver.py) and records the outcome. `payload` is the event body
exactly as it is sent.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE event (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            api text NOT NULL CHECK (api ~ '^TMF[0-9]{3}$'),
            event_type text NOT NULL CHECK (event_type ~ '^[A-Z][A-Za-z]+Event$'),
            resource text NOT NULL CHECK (length(resource) > 0),
            resource_id text NOT NULL CHECK (length(resource_id) > 0),
            occurred_at timestamptz NOT NULL,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
        );
        CREATE INDEX event_occurred_idx ON event (occurred_at);
        CREATE TYPE event_delivery_state AS ENUM ('pending', 'delivered', 'failed', 'dropped');
        CREATE TABLE event_delivery (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            event_id uuid NOT NULL REFERENCES event ON DELETE CASCADE,
            subscription_id uuid NOT NULL REFERENCES event_subscription,
            state event_delivery_state NOT NULL DEFAULT 'pending',
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_attempt_at timestamptz,
            last_error text,
            finished_at timestamptz,
            UNIQUE (event_id, subscription_id),
            CONSTRAINT event_delivery_pending_is_due
                CHECK ((state = 'pending') = (next_attempt_at IS NOT NULL)),
            CONSTRAINT event_delivery_finished
                CHECK ((state = 'pending') = (finished_at IS NULL))
        );
        CREATE INDEX event_delivery_due_idx ON event_delivery (next_attempt_at)
            WHERE state = 'pending';
        CREATE INDEX event_delivery_subscription_idx ON event_delivery (subscription_id);
        COMMENT ON TABLE event IS
            'TMF events some hub wanted, as sent; stored in the transaction of the change.';
        COMMENT ON TABLE event_delivery IS
            'One event to one subscription: pending until the worker delivers it or gives up.';
        COMMENT ON TYPE event_delivery_state IS
            'Ours: pending (due at next_attempt_at), delivered (2xx), failed (attempts used up),'
            ' dropped (its hub was deleted first).';
    """)
    comments = {
        "event": {
            "id": "Server-generated identifier; the eventId sent.",
            "seq": "Insertion order: events of one transaction keep the order they happened in.",
            "created_at": "Database insertion time.",
            "api": "The API whose hubs it goes to (TMF620 ... TMF678).",
            "event_type": "TMF listener event name, e.g. ProductOrderCreateEvent.",
            "resource": "The payload key: the resource, camelCase (productOrder).",
            "resource_id": "Id of the resource the event is about.",
            "occurred_at": "When the change happened (application clock); eventTime.",
            "payload": "The event body exactly as POSTed to the callbacks.",
        },
        "event_delivery": {
            "id": "Server-generated identifier.",
            "created_at": "Database insertion time.",
            "event_id": "The event to send.",
            "subscription_id": "The hub whose callback receives it.",
            "state": "pending, delivered, failed or dropped (event_delivery_state).",
            "attempts": "POSTs tried so far.",
            "next_attempt_at": "When the worker tries next; set exactly while pending.",
            "last_attempt_at": "When the last POST was tried.",
            "last_error": "Why the last POST did not succeed (status or error), if it did not.",
            "finished_at": "When it stopped being pending; NULL exactly while pending.",
        },
    }
    for table, columns in comments.items():
        for column, meaning in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        DROP TABLE event_delivery;
        DROP TYPE event_delivery_state;
        DROP TABLE event;
    """)
