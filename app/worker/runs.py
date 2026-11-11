"""What starting the billing run of a date does (docs/archive/stage4-design.md, section 2).

The caller is the worker, on its own connection (db.py). Ownership comes first: if another
live worker holds the lock, RunInProgress - before anything is read. With the lock held,
the run's row decides:

    none        insert a running run                      -> Started("new")
    completed   nothing to do                             -> AlreadyCompleted
    failed      reopen it: attempt + 1, failure cleared   -> Started("reopened")
    running     its owner is gone (we hold the lock):     -> Started("takenOver")
                take it over, attempt + 1, trigger kept,
                started_at = now (the attempt's start)

Every start counts the run's members into member_count in the same transaction (#284).

A takeover is safe because bill_account is idempotent and writes one account per
transaction; the per-account outcome rows (slice b2) keep ok accounts untouched.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import psycopg

from app.repository import billing
from app.repository._sql import Row
from app.worker import db


class RunInProgress(Exception):
    """Another live worker owns billing; back off."""


@dataclass(frozen=True)
class Started:
    run: Row
    how: Literal["new", "reopened", "takenOver"]


@dataclass(frozen=True)
class AlreadyCompleted:
    run: Row


def ensure_ownership(conn: psycopg.Connection) -> None:
    if not db.holds_ownership(conn) and not db.acquire_ownership(conn):
        raise RunInProgress("another worker owns billing")


def start_run(
    conn: psycopg.Connection,
    run_date: date,
    *,
    now: datetime,
    trigger: str,
    tz: str | None = None,
) -> Started | AlreadyCompleted:
    ensure_ownership(conn)  # before anything is read
    with conn.transaction():
        run = billing.lock_run(conn, run_date)
        if run is None:
            new = billing.insert_run(conn, run_date, now=now, trigger=trigger)
            return Started(billing.count_members(conn, new["id"], tz), "new")
        if run["status"] == "completed":
            return AlreadyCompleted(run)
        if run["status"] == "failed":
            billing.reopen_run(conn, run["id"], now=now, trigger=trigger)
            return Started(billing.count_members(conn, run["id"], tz), "reopened")
        # running, and we hold the lock: whoever started it is gone
        billing.take_over_run(conn, run["id"], now=now)
        return Started(billing.count_members(conn, run["id"], tz), "takenOver")
