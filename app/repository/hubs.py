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


def matching(conn: psycopg.Connection, api: str, event_type: str) -> list[Row]:
    """The live subscriptions of `api` that want `event_type` (or every event)."""
    return fetch_all(
        conn,
        """
        SELECT * FROM event_subscription
         WHERE api = %s AND deleted_at IS NULL
           AND (event_types IS NULL OR %s = ANY(event_types))
         ORDER BY created_at, id
        """,
        (api, event_type),
    )
