"""TMF hub: event subscriptions (POST/DELETE /hub, GET /hub/{id} in TMF629).

One table for the hubs of every API (`api` keeps them apart; each has its own path). A
subscription names a callback and, optionally, the event types it wants (`eventType=`
in the hub's query; NULL = every event of its API). DELETE marks it deleted instead of
removing it, so deliveries already made can still name it.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE event_subscription (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            api text NOT NULL CHECK (api ~ '^TMF[0-9]{3}$'),
            callback text NOT NULL CHECK (length(callback) > 0),
            query text CHECK (length(query) > 0),
            event_types text[] CHECK (cardinality(event_types) > 0),
            subscribed_at timestamptz NOT NULL,
            deleted_at timestamptz,
            CONSTRAINT event_subscription_types_from_query
                CHECK ((query IS NULL) = (event_types IS NULL)),
            CONSTRAINT event_subscription_deleted_after_subscribed
                CHECK (deleted_at >= subscribed_at)
        );
        CREATE INDEX event_subscription_live_idx ON event_subscription (api)
            WHERE deleted_at IS NULL;
        COMMENT ON TABLE event_subscription IS
            'TMF hub subscriptions of every API; deleted ones are kept (deleted_at) for their deliveries.';
    """)
    for column, meaning in {
        "id": "Server-generated identifier: the hub id.",
        "created_at": "Database insertion time; stable ordering.",
        "api": "The API whose /hub created it (TMF620 ... TMF678); only its events match.",
        "callback": "Where events are POSTed: absolute URL, checked on create and on every send.",
        "query": "The hub query as sent (eventType=A,B); NULL = every event of the API.",
        "event_types": "The event types parsed from query; NULL exactly when query is.",
        "subscribed_at": "When the hub was created (application clock).",
        "deleted_at": "When DELETE /hub removed it; NULL while live.",
    }.items():
        op.execute(f"COMMENT ON COLUMN event_subscription.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("DROP TABLE event_subscription")
