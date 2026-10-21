"""Durable command journal foundation. No public or worker caller yet.

Call within a transaction. Acquire command identity before account/child locks,
using the same advisory identity as funding holds. Future execution must take the
worker ownership lock first and commit effects, result and events together. Record
technical failure only in a fresh transaction after rolling back those effects.
"""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_one, jsonb, returning


def get(conn: psycopg.Connection, command_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM prepaid_command WHERE id = %s", (command_id,))


def lock_identity(conn: psycopg.Connection, command_id: UUID) -> None:
    if not isinstance(command_id, UUID):
        raise ValueError("command identity must be a UUID")
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"funding-command:{command_id}",),
    )


def enqueue(
    conn: psycopg.Connection,
    command_id: UUID,
    account_id: UUID,
    *,
    kind: str,
    due_date: date,
    request: dict[str, Any],
    now: datetime,
) -> Row:
    """Persist once; retries return the stored row, changed requests are refused.

    The producer validates its kind-specific quote; this journal stores it without
    interpreting prices or purchasing service. Acceptance time is not request identity.
    """
    lock_identity(conn, command_id)
    existing = get(conn, command_id)
    if existing is not None:
        matches = conn.execute(
            "SELECT billing_account_id = %s AND kind = %s AND due_date = %s AND request = %s"
            " FROM prepaid_command WHERE id = %s",
            (account_id, kind, due_date, jsonb(request), command_id),
        ).fetchone()[0]
        if not matches:
            raise ValueError("prepaid command request mismatch")
        return existing
    return returning(
        conn,
        "INSERT INTO prepaid_command (id, billing_account_id, kind, due_date, request, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (command_id, account_id, kind, due_date, jsonb(request), now),
    )


def _locked(conn: psycopg.Connection, command_id: UUID, now: datetime) -> Row:
    lock_identity(conn, command_id)
    row = get(conn, command_id)
    if row is None:
        raise ValueError("unknown prepaid command")
    if now < (row["attempted_at"] or row["created_at"]):
        raise ValueError("command decision precedes recorded history")
    return row


def ready(conn: psycopg.Connection, command_id: UUID, *, now: datetime, today: date) -> Row:
    """Serialize an eligible attempt; no persisted running state is needed.

    A killed transaction leaves the prior state and owned holds intact. The caller
    must keep this transaction/identity lock through its effect and result writes.
    """
    row = _locked(conn, command_id, now)
    if row["state"] == "completed":
        return row
    if row["due_date"] > today or (row["next_retry_at"] is not None and row["next_retry_at"] > now):
        raise ValueError("prepaid command is not due")
    return row


def complete(
    conn: psycopg.Connection, command_id: UUID, result: dict[str, Any], *, now: datetime
) -> Row:
    """Commit a business outcome with its effects; completed retries retain the result."""
    row = _locked(conn, command_id, now)
    if row["state"] == "completed":
        return row
    return returning(
        conn,
        "UPDATE prepaid_command SET state = 'completed', result = %s, error = NULL,"
        " next_retry_at = NULL, attempts = attempts + 1, attempted_at = %s"
        " WHERE id = %s RETURNING *",
        (jsonb(result), now, command_id),
    )


def fail(conn: psycopg.Connection, command_id: UUID, error: str, *, now: datetime) -> Row:
    """Record technical failure after rollback; never release the command's hold."""
    if not error:
        raise ValueError("technical failure requires a reason")
    row = _locked(conn, command_id, now)
    if row["state"] == "completed":
        return row
    delay = (1, 5, 60)[min(row["attempts"], 2)]
    return returning(
        conn,
        "UPDATE prepaid_command SET state = 'failedTechnical', error = %s,"
        " attempts = attempts + 1, attempted_at = %s, next_retry_at = %s"
        " WHERE id = %s RETURNING *",
        (error, now, now + timedelta(minutes=delay), command_id),
    )


def retry(conn: psycopg.Connection, command_id: UUID, *, now: datetime) -> Row:
    """Explicit technical retry retains identity, request, hold and attempt count."""
    row = _locked(conn, command_id, now)
    if row["state"] != "failedTechnical":
        raise ValueError("only failedTechnical commands can be retried")
    return returning(
        conn,
        "UPDATE prepaid_command SET state = 'pending', error = NULL, next_retry_at = NULL"
        " WHERE id = %s RETURNING *",
        (command_id,),
    )
