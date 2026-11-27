"""TMF hub subscriptions (event_subscription, migration 0017)."""

from datetime import datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning


def create(
    conn: psycopg.Connection,
    *,
    api: str,
    callback: str,
    query: str | None,
    event_types: list[str] | None,
    at: datetime,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO event_subscription (api, callback, query, event_types, subscribed_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (api, callback, query, event_types, at),
    )


def get_live(conn: psycopg.Connection, api: str, uid: UUID, *, lock: bool = False) -> Row | None:
    """A subscription of this API that was not deleted."""
    query = "SELECT * FROM event_subscription WHERE id = %s AND api = %s AND deleted_at IS NULL"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (uid, api))


def mark_deleted(conn: psycopg.Connection, uid: UUID, at: datetime) -> Row:
    return returning(
        conn, "UPDATE event_subscription SET deleted_at = %s WHERE id = %s RETURNING *", (at, uid)
    )


def page(
    conn: psycopg.Connection, *, api: str | None, offset: int, limit: int
) -> tuple[list[Row], bool]:
    """Live subscriptions newest first (ties by id), each with its delivery summary:
    the last attempted delivery (state, time) and how many deliveries failed for good."""
    params = {"api": api, "offset": offset, "limit": limit + 1}
    rows = fetch_all(
        conn,
        """
        SELECT s.*, last.state AS last_state, last.at AS last_at,
               (SELECT count(*) FROM event_delivery f
                 WHERE f.subscription_id = s.id AND f.state = 'failed') AS failures
          FROM event_subscription s
          LEFT JOIN LATERAL (
              SELECT d.state, d.last_attempt_at AS at FROM event_delivery d
               WHERE d.subscription_id = s.id AND d.last_attempt_at IS NOT NULL
               ORDER BY d.last_attempt_at DESC, d.id DESC LIMIT 1) last ON true
         WHERE s.deleted_at IS NULL AND (%(api)s::text IS NULL OR s.api = %(api)s)
         ORDER BY s.subscribed_at DESC, s.id DESC OFFSET %(offset)s LIMIT %(limit)s
        """,
        params,
    )
    return rows[:limit], len(rows) > limit
