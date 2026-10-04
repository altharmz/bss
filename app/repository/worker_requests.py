"""The UI's requests to the billing worker (worker_request, migration 0004): catch up to a
date, retry a failed run. The internal API only inserts them; the worker picks them up,
oldest first, and marks them done."""

from datetime import date, datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_one, returning


def insert_request(
    conn: psycopg.Connection,
    *,
    kind: str,
    requested_at: datetime,
    requested_by: str | None,
    up_to: date | None = None,
    run_id: UUID | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO worker_request (kind, up_to, run_id, requested_at, requested_by)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (kind, up_to, run_id, requested_at, requested_by),
    )


def pending_retry(conn: psycopg.Connection, run_id: UUID) -> Row | None:
    """A retry of this run that the worker has not finished yet."""
    return fetch_one(
        conn,
        "SELECT * FROM worker_request WHERE kind = 'retry' AND run_id = %s AND done_at IS NULL",
        (run_id,),
    )


def next_pending(conn: psycopg.Connection) -> Row | None:
    """The oldest request not done yet, locked (a request picked by a worker that died
    before finishing it is picked again). Ties of requested_at (the injected clock: two
    requests in the same instant) are broken by insertion order (created_at, the
    database's time of the inserting transaction) - never by the random id."""
    return fetch_one(
        conn,
        """
        SELECT * FROM worker_request WHERE done_at IS NULL
         ORDER BY requested_at, created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
        """,
    )


def mark_picked(conn: psycopg.Connection, request_id: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE worker_request SET picked_at = %s, updated_at = now() WHERE id = %s RETURNING *",
        (now, request_id),
    )


def mark_done(conn: psycopg.Connection, request_id: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE worker_request SET done_at = %s, updated_at = now() WHERE id = %s RETURNING *",
        (now, request_id),
    )


def get_request(conn: psycopg.Connection, request_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM worker_request WHERE id = %s", (request_id,))
