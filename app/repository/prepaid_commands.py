"""Durable command journal used by internal posting and worker dispatch.

Call within a transaction. Acquire command identity before account/child locks,
using the same advisory identity as funding holds. Future execution must take the
worker ownership lock first and commit effects, result and events together. Record
technical failure only in a fresh transaction after rolling back those effects.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning

# Renewal identity derives from its predecessor grant, so production is idempotent.
RENEWAL_NAMESPACE = UUID("6f1c0b7e-3d4a-5e8b-9c2d-1a7f4e6b8c90")


def renewal_id(interval_id: UUID) -> UUID:
    return uuid5(RENEWAL_NAMESPACE, str(interval_id))


def get(conn: psycopg.Connection, command_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM prepaid_command WHERE id = %s", (command_id,))


def ensure_run(conn: psycopg.Connection, command_id: UUID, *, run_date: date, now: datetime) -> Row:
    """Retain one real run per command, in the caller's effects transaction.

    The billing owner must hold ownership first. Replays keep the original date,
    including when a retry arrives on a later business date. This foundation does
    not dispatch, complete or recover a command run.
    """
    lock_identity(conn, command_id)
    if get(conn, command_id) is None:
        raise ValueError("unknown prepaid command")
    existing = fetch_one(conn, "SELECT * FROM billing_run WHERE command_id = %s", (command_id,))
    if existing is not None:
        return existing
    return returning(
        conn,
        "INSERT INTO billing_run (kind, command_id, run_date, status, started_at, trigger)"
        " VALUES ('prepaidCommand', %s, %s, 'running', %s, 'scheduled') RETURNING *",
        (command_id, run_date, now),
    )


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
    latest = fetch_one(
        conn,
        "SELECT observed_at FROM prepaid_funding_shortfall WHERE command_id = %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (command_id,),
    )
    if latest is not None and now < latest["observed_at"]:
        raise ValueError("command decision precedes recorded funding history")
    return row


def needs_topup(conn: psycopg.Connection, command_id: UUID) -> Row | None:
    """Latest funding reason, only while pending; retained history survives recovery."""
    return fetch_one(
        conn,
        "SELECT s.* FROM prepaid_funding_shortfall s JOIN prepaid_command c ON c.id = s.command_id"
        " WHERE c.id = %s AND c.state = 'pending' ORDER BY s.observed_at DESC LIMIT 1",
        (command_id,),
    )


def record_shortfall(
    conn: psycopg.Connection,
    command_id: UUID,
    *,
    required: Decimal,
    available: Decimal,
    now: datetime,
) -> None:
    """Retain a business decision after posting rollback, without technical backoff."""
    row = _locked(conn, command_id, now)
    if row["state"] == "completed":
        return
    if not (required.is_finite() and available.is_finite() and required > available >= 0):
        raise ValueError("invalid funding shortfall")
    if row["state"] == "failedTechnical":
        retry(conn, command_id, now=now)
    existing = fetch_one(
        conn,
        "SELECT * FROM prepaid_funding_shortfall WHERE command_id = %s AND observed_at = %s",
        (command_id, now),
    )
    if existing is not None:
        if (existing["required"], existing["available"]) != (required, available):
            raise ValueError("funding decision mismatch")
        return
    conn.execute(
        "INSERT INTO prepaid_funding_shortfall (command_id, observed_at, required, available)"
        " VALUES (%s, %s, %s, %s)",
        (command_id, now, required, available),
    )


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


# The latest funding observation of command `c` (the dispatcher's retry scan).
_LATEST_SHORTFALL = (
    " LEFT JOIN LATERAL (SELECT observed_at, required FROM prepaid_funding_shortfall"
    " WHERE command_id = c.id ORDER BY observed_at DESC LIMIT 1) s ON true"
)
# Why a pending command is attempted again before its next business date: never attempted
# since its latest shortfall, its product ends, or a credit reached its account after it:
# a done payment (a top-up records one), a released hold, or a funding source (an
# adjustment credit, an incoming transfer, a debit's reversal), #340.
_SIGNALLED = (
    "coalesce(s.observed_at IS NULL OR c.attempted_at > s.observed_at"
    " OR p.termination_date <= %(today)s"
    " OR EXISTS (SELECT FROM payment y WHERE y.billing_account_id = c.billing_account_id"
    " AND y.status = 'done' AND y.received_at > s.observed_at)"
    " OR EXISTS (SELECT FROM funding_hold h WHERE h.billing_account_id = c.billing_account_id"
    " AND h.released_at > s.observed_at)"
    " OR EXISTS (SELECT FROM funding_source fs"
    " WHERE fs.billing_account_id = c.billing_account_id AND fs.available_at > s.observed_at),"
    " false)"
)
_EARLIER_DATE = "(s.observed_at AT TIME ZONE %(tz)s)::date < %(today)s"
_DUE = (
    " FROM prepaid_command c LEFT JOIN funded_interval f"
    " ON f.id = (c.request->>'previous_interval_id')::uuid"
    " LEFT JOIN product p ON p.id = f.product_id"
    + _LATEST_SHORTFALL
    + " WHERE c.state <> 'completed'"
    " AND c.kind IN ('order', 'renewal') AND c.due_date <= %(today)s"
    " AND (c.next_retry_at IS NULL OR c.next_retry_at <= %(now)s)"
)


def candidates(conn: psycopg.Connection, *, now: datetime, today: date, tz: str) -> list[Row]:
    """The orders and renewals to attempt this wake-up, per account in posting order, each
    with `signalled` (see _SIGNALLED); the others are due again only by the business date."""
    return fetch_all(
        conn,
        "SELECT c.*, " + _SIGNALLED + " AS signalled" + _DUE + " AND (" + _SIGNALLED + " OR "
        + _EARLIER_DATE + ") ORDER BY c.billing_account_id, c.due_date,"
        " COALESCE(c.request->>'product_id', f.product_id::text, c.id::text), c.id",
        {"today": today, "now": now, "tz": tz},
    )  # fmt: skip


def date_only_retries(
    conn: psycopg.Connection, *, now: datetime, today: date, tz: str
) -> list[Row]:
    """Pending renewals due again only because the business date changed since their
    latest shortfall (no credit signal), whose product and account would let them post
    (an ended, blocked or network-waiting one goes through the per-account path).
    Each row has the command `id`, its `billing_account_id` and the latest `required`."""
    return fetch_all(
        conn,
        "SELECT c.id, c.billing_account_id, s.required" + _DUE + " AND c.kind = 'renewal'"  # noqa: S608 - fixed SQL fragments
        " AND c.state = 'pending' AND " + _EARLIER_DATE + " AND NOT " + _SIGNALLED +
        " AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
        " AND (p.termination_date IS NULL OR p.termination_date > %(today)s)"
        " AND NOT (p.manually_suspended AND NOT p.lifecycle_suspended)"
        " AND EXISTS (SELECT FROM billing_account a WHERE a.id = c.billing_account_id"
        " AND a.state = 'Active')"
        " AND NOT EXISTS (SELECT FROM network_command n WHERE n.product_id = p.id"
        " AND n.kind = 'change_profile' AND n.state IN ('pending', 'exhausted', 'refused')"
        " AND EXISTS (SELECT FROM prepaid_command o WHERE o.id = n.product_order_id"
        " AND o.state <> 'completed'))"
        " ORDER BY c.billing_account_id, c.id",
        {"today": today, "now": now, "tz": tz},
    )  # fmt: skip


def record_daily_shortfalls(
    conn: psycopg.Connection, available: dict[UUID, Decimal], *, now: datetime
) -> set[UUID]:
    """Today's observation of each still-short date-only renewal in one statement (#340):
    its latest `required` carried over, `available` the spendable balance given for it.
    Nothing is locked or posted. A row that would break the table's rules (an observation
    at `now` already, or `available` not below `required`) is not written; the caller's
    per-account path decides those. Returns the commands written."""
    if not available:
        return set()
    ids = list(available)
    rows = conn.execute(
        "INSERT INTO prepaid_funding_shortfall (command_id, observed_at, required, available)"
        " SELECT v.id, %s, s.required, v.available"
        " FROM unnest(%s::uuid[], %s::numeric[]) AS v(id, available)"
        " JOIN prepaid_command c ON c.id = v.id AND c.state = 'pending'"
        " JOIN LATERAL (SELECT observed_at, required FROM prepaid_funding_shortfall"
        " WHERE command_id = v.id ORDER BY observed_at DESC LIMIT 1) s ON true"
        " WHERE s.required > v.available AND v.available >= 0 AND s.observed_at < %s"
        " ON CONFLICT (command_id, observed_at) DO NOTHING RETURNING command_id",
        (now, ids, [available[i] for i in ids], now),
    ).fetchall()
    return {row[0] for row in rows}
