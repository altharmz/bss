"""Events and deliveries (migration 0018): the outbox the worker sends from."""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.repository._sql import Row, fetch_all, fetch_one, returning


def wanted_by(conn: psycopg.Connection, api: str, event_types: list[str]) -> set[str]:
    """Which of these event types at least one live hub of `api` wants."""
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT t AS event_type FROM unnest(%s::text[]) t
         WHERE EXISTS (SELECT 1 FROM event_subscription s
                        WHERE s.api = %s AND s.deleted_at IS NULL
                          AND (s.event_types IS NULL OR t = ANY(s.event_types)))
        """,
        (event_types, api),
    )
    return {row["event_type"] for row in rows}


def store(
    conn: psycopg.Connection,
    *,
    event_id: UUID,
    api: str,
    event_type: str,
    resource: str,
    resource_id: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> int:
    """Store the event with a pending delivery for every live hub that wants it, due at
    once. Returns the number of deliveries (0: nothing stored)."""
    subscribers = fetch_all(
        conn,
        """
        SELECT id FROM event_subscription
         WHERE api = %s AND deleted_at IS NULL
           AND (event_types IS NULL OR %s = ANY(event_types))
         ORDER BY created_at, id
        """,
        (api, event_type),
    )
    if not subscribers:
        return 0
    from app.api import tmfjson  # lazily: importing app.api loads every API module

    conn.execute(
        """
        INSERT INTO event (id, api, event_type, resource, resource_id, occurred_at, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        # tmfjson: money stays an exact decimal number, never a float
        (
            event_id,
            api,
            event_type,
            resource,
            resource_id,
            occurred_at,
            Jsonb(payload, dumps=tmfjson.dumps),
        ),
    )
    for row in subscribers:
        conn.execute(
            "INSERT INTO event_delivery (event_id, subscription_id, next_attempt_at)"
            " VALUES (%s, %s, %s)",
            (event_id, row["id"], occurred_at),
        )
    return len(subscribers)


def drop_pending(conn: psycopg.Connection, subscription_id: UUID, at: datetime) -> int:
    """A deleted hub receives nothing more: its pending deliveries are dropped."""
    rows = fetch_all(
        conn,
        """
        UPDATE event_delivery
           SET state = 'dropped', next_attempt_at = NULL, finished_at = %s
         WHERE subscription_id = %s AND state = 'pending' RETURNING id
        """,
        (at, subscription_id),
    )
    return len(rows)


def due(conn: psycopg.Connection, now: datetime, limit: int) -> list[Row]:
    """Pending deliveries due by `now`, oldest first, locked (SKIP LOCKED: a second sender
    never takes the same one), with their callback and the event as JSON text (`body`:
    sent as is - parsing jsonb would turn money into floats)."""
    return fetch_all(
        conn,
        """
        SELECT d.id, d.attempts, d.event_id, e.payload::text AS body, e.event_type,
               s.callback, s.id AS hub_id
          FROM event_delivery d
          JOIN event e ON e.id = d.event_id
          JOIN event_subscription s ON s.id = d.subscription_id
         WHERE d.state = 'pending' AND d.next_attempt_at <= %s
         ORDER BY d.next_attempt_at, e.seq, d.id
         LIMIT %s
         FOR UPDATE OF d SKIP LOCKED
        """,
        (now, limit),
    )


def delivered(conn: psycopg.Connection, delivery_id: UUID, at: datetime) -> Row:
    return returning(
        conn,
        """
        UPDATE event_delivery
           SET state = 'delivered', attempts = attempts + 1, next_attempt_at = NULL,
               last_attempt_at = %s, last_error = NULL, finished_at = %s
         WHERE id = %s RETURNING *
        """,
        (at, at, delivery_id),
    )


def attempt_failed(
    conn: psycopg.Connection,
    delivery_id: UUID,
    *,
    at: datetime,
    error: str,
    retry_at: datetime | None,
) -> Row:
    """Record a failed POST: due again at `retry_at`, or failed for good (None)."""
    return returning(
        conn,
        """
        UPDATE event_delivery
           SET attempts = attempts + 1, last_attempt_at = %(at)s, last_error = %(error)s,
               state = CASE WHEN %(retry)s::timestamptz IS NULL THEN 'failed'
                            ELSE 'pending' END::event_delivery_state,
               next_attempt_at = %(retry)s,
               finished_at = CASE WHEN %(retry)s::timestamptz IS NULL THEN %(at)s END
         WHERE id = %(id)s RETURNING *
        """,
        {"at": at, "error": error[:1000], "retry": retry_at, "id": delivery_id},
    )


def purge(conn: psycopg.Connection, before: datetime) -> int:
    """Remove events every delivery of which finished before `before` (with them)."""
    rows = fetch_all(
        conn,
        """
        DELETE FROM event e
         WHERE e.occurred_at < %s
           AND NOT EXISTS (SELECT 1 FROM event_delivery d
                            WHERE d.event_id = e.id
                              AND (d.state = 'pending' OR d.finished_at >= %s))
        RETURNING id
        """,
        (before, before),
    )
    return len(rows)


def get_delivery(conn: psycopg.Connection, delivery_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM event_delivery WHERE id = %s", (delivery_id,))
