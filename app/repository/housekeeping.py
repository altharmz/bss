"""Retention: the rows the worker's housekeeping deletes, and the usage_record partitions
(#282; docs/decisions/data-model.md "Retention", usage.md "Partitions").

Every `delete_*` deletes at most `batch` rows, oldest first, in the caller's transaction,
and returns how many it deleted. Nothing here deletes a row of the kept-forever list.
"""

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

import psycopg

from app.repository._sql import fetch_all, fetch_one

# how long each table keeps its rows (the human's decision of 2026-11-09)
RUN_OUTCOMES_DAYS = 90  # ok outcomes of completed runs; the counts stay on billing_run
SESSION_GRACE = timedelta(days=1)  # past the absolute or the idle expiry
IDEMPOTENCY_DAYS = 30  # a replay after that is a new request
DELIVERY_DAYS = 30  # finished event deliveries
EVENT_DAYS = 90  # events no delivery refers to any more
WORKER_REQUEST_DAYS = 90  # done requests
NOTIFICATION_MONTHS = 13
QUALIFICATION_DAYS = 30  # TMF679 answers: they expire the day they are given (#429)
USAGE_MONTHS = 13  # usage_record partitions
USAGE_AHEAD_MONTHS = 2  # partitions made ahead of the current month

PARTITION = re.compile(r"^usage_record_y(\d{4})m(\d{2})$")


def running_run(conn: psycopg.Connection) -> bool:
    return (
        fetch_one(conn, "SELECT 1 AS x FROM billing_run WHERE status = 'running' LIMIT 1")
        is not None
    )


def delete_run_outcomes(conn: psycopg.Connection, *, today: date, batch: int, **_: object) -> int:
    """The ok rows of the oldest completed run dated more than 90 days ago that still has
    some. Non-ok rows drive `blocked` and stay; a completed run has none anyway."""
    run = fetch_one(
        conn,
        "SELECT r.id FROM billing_run r WHERE r.status = 'completed' AND r.run_date < %s"
        " AND EXISTS (SELECT 1 FROM billing_run_account a"
        "             WHERE a.run_id = r.id AND a.status = 'ok')"
        " ORDER BY r.run_date, r.id LIMIT 1",
        (today - timedelta(days=RUN_OUTCOMES_DAYS),),
    )
    if run is None:
        return 0
    return _count(
        conn,
        "DELETE FROM billing_run_account WHERE run_id = %(run)s AND billing_account_id IN"
        " (SELECT billing_account_id FROM billing_run_account"
        "   WHERE run_id = %(run)s AND status = 'ok'"
        "   ORDER BY billing_account_id LIMIT %(batch)s)",
        {"run": run["id"], "batch": batch},
    )


def delete_sessions(
    conn: psycopg.Connection, *, now: datetime, idle: timedelta, batch: int, **_: object
) -> int:
    """Sessions whose absolute (expires_at) or idle expiry passed more than a day ago."""
    return _count(
        conn,
        "DELETE FROM app_session WHERE token_hash IN"
        " (SELECT token_hash FROM app_session"
        "   WHERE expires_at < %(cut)s OR last_seen_at < %(idle_cut)s"
        "   ORDER BY least(expires_at, last_seen_at + %(idle)s) LIMIT %(batch)s)",
        {
            "cut": now - SESSION_GRACE,
            "idle_cut": now - SESSION_GRACE - idle,
            "idle": idle,
            "batch": batch,
        },
    )


def delete_idempotency_keys(
    conn: psycopg.Connection, *, now: datetime, batch: int, **_: object
) -> int:
    """Keys stored more than 30 days ago, tombstoned or not. The table's trigger refuses
    every delete except inside a housekeeping transaction."""
    conn.execute("SET LOCAL bss.housekeeping = 'on'")
    return _count(
        conn,
        "DELETE FROM idempotency_key WHERE (operator, method, route, key) IN"
        " (SELECT operator, method, route, key FROM idempotency_key"
        "   WHERE created_at < %(cut)s ORDER BY created_at LIMIT %(batch)s)",
        {"cut": now - timedelta(days=IDEMPOTENCY_DAYS), "batch": batch},
    )


def delete_deliveries(conn: psycopg.Connection, *, now: datetime, batch: int, **_: object) -> int:
    """Event deliveries that finished (delivered, failed or dropped) more than 30 days ago."""
    return _count(
        conn,
        "DELETE FROM event_delivery WHERE id IN"
        " (SELECT id FROM event_delivery WHERE state <> 'pending' AND finished_at < %(cut)s"
        "   ORDER BY finished_at LIMIT %(batch)s)",
        {"cut": now - timedelta(days=DELIVERY_DAYS), "batch": batch},
    )


def delete_events(conn: psycopg.Connection, *, now: datetime, batch: int, **_: object) -> int:
    """Events that happened more than 90 days ago and that no delivery refers to."""
    return _count(
        conn,
        "DELETE FROM event WHERE id IN"
        " (SELECT e.id FROM event e WHERE e.occurred_at < %(cut)s"
        "   AND NOT EXISTS (SELECT 1 FROM event_delivery d WHERE d.event_id = e.id)"
        "   ORDER BY e.occurred_at LIMIT %(batch)s)",
        {"cut": now - timedelta(days=EVENT_DAYS), "batch": batch},
    )


def delete_worker_requests(
    conn: psycopg.Connection, *, now: datetime, batch: int, **_: object
) -> int:
    """Requests the worker finished more than 90 days ago."""
    return _count(
        conn,
        "DELETE FROM worker_request WHERE id IN"
        " (SELECT id FROM worker_request WHERE done_at < %(cut)s"
        "   ORDER BY done_at LIMIT %(batch)s)",
        {"cut": now - timedelta(days=WORKER_REQUEST_DAYS), "batch": batch},
    )


def delete_notifications(
    conn: psycopg.Connection, *, now: datetime, batch: int, **_: object
) -> int:
    """Messages created more than 13 months ago. One that a kept resend refers to
    (resend_of) stays until that resend goes: it is the resend's original."""
    return _count(
        conn,
        "DELETE FROM notification_message WHERE id IN"
        " (SELECT m.id FROM notification_message m"
        "   WHERE m.created_at < %(now)s - make_interval(months => %(months)s)"
        "   AND NOT EXISTS (SELECT 1 FROM notification_message r WHERE r.resend_of = m.id)"
        "   ORDER BY m.created_at LIMIT %(batch)s)",
        {"now": now, "months": NOTIFICATION_MONTHS, "batch": batch},
    )


def delete_qualifications(
    conn: psycopg.Connection, *, now: datetime, batch: int, **_: object
) -> int:
    """Product offering qualifications created more than 30 days ago (each expired at the
    end of its own day; docs/decisions/qualification.md)."""
    return _count(
        conn,
        "DELETE FROM product_offering_qualification WHERE id IN"
        " (SELECT id FROM product_offering_qualification WHERE created_at < %(cut)s"
        "   ORDER BY created_at LIMIT %(batch)s)",
        {"cut": now - timedelta(days=QUALIFICATION_DAYS), "batch": batch},
    )


# in this order: deliveries before events, so an event freed today goes today
RULES: tuple[tuple[str, Callable[..., int]], ...] = (
    ("billing_run_account", delete_run_outcomes),
    ("app_session", delete_sessions),
    ("idempotency_key", delete_idempotency_keys),
    ("event_delivery", delete_deliveries),
    ("event", delete_events),
    ("worker_request", delete_worker_requests),
    ("notification_message", delete_notifications),
    ("product_offering_qualification", delete_qualifications),
)


# --- usage_record partitions ---


def month_start(day: date, months: int = 0) -> date:
    """The first day of the month `months` after (before, if negative) that of `day`."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def create_partition(conn: psycopg.Connection, month: date) -> bool:
    """The partition of the month of `month`; False when it exists. Rows the DEFAULT
    partition holds for that month move into it (usage_record_partition, migration 0076)."""
    row = fetch_one(conn, "SELECT usage_record_partition(%s) AS created", (month,))
    assert row is not None
    return row["created"]


def partitions(conn: psycopg.Connection) -> dict[str, date]:
    """The monthly partitions of usage_record: name -> first day of the month."""
    rows = fetch_all(
        conn,
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
        " WHERE i.inhparent = 'usage_record'::regclass",
    )
    out = {}
    for row in rows:
        m = PARTITION.match(row["relname"])
        if m:
            out[row["relname"]] = date(int(m.group(1)), int(m.group(2)), 1)
    return out


def drop_partition(conn: psycopg.Connection, name: str) -> None:
    assert PARTITION.match(name), name
    conn.execute(f'DROP TABLE "{name}"')


def default_rows(conn: psycopg.Connection) -> int:
    row = fetch_one(conn, "SELECT count(*) AS n FROM usage_record_default")
    assert row is not None
    return row["n"]


def _count(conn: psycopg.Connection, query: str, params: dict) -> int:
    return conn.execute(query, params).rowcount  # type: ignore[arg-type]
