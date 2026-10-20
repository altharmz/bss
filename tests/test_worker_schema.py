"""Migration 0004: what the schema itself refuses for the billing worker's tables.

The worker's own rules (a blocked row waits for an EARLIER run, "completed" only when
every member account is ok) cannot be CHECKs - they compare rows - and are proven in the
worker's tests. Here: one valid row per table, then every constraint broken once.
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from psycopg import errors

from app.repository import billing
from tests.factories import NOW, account, rejected

D = date(2026, 2, 1)


def new_run(conn, day):
    return billing.insert_run(conn, day, now=NOW, trigger="scheduled")


@pytest.fixture
def run(db_conn):
    return new_run(db_conn, D)


def outcome(conn, run_id, account_id, status, *, error=None, waits_for=None, attempts=1):
    conn.execute(
        """
        INSERT INTO billing_run_account
            (run_id, billing_account_id, status, error, waits_for_run_id, attempts, finished_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (run_id, account_id, status, error, waits_for, attempts, NOW),
    )


def request(conn, kind, *, up_to=None, run_id=None, picked=None, done=None):
    conn.execute(
        """
        INSERT INTO worker_request (kind, up_to, run_id, requested_at, picked_at, done_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (kind, up_to, run_id, NOW, picked, done),
    )


# --- billing_run ---


def test_billing_run_trigger_has_no_default(db_conn):
    with rejected(db_conn, errors.NotNullViolation):
        db_conn.execute(
            "INSERT INTO billing_run (run_date, status, started_at) VALUES (%s, 'running', %s)",
            (D, NOW),
        )


@pytest.mark.parametrize(
    ("status", "failure_kind", "finished"),
    [("failed", None, True), ("completed", "run", True), ("running", "accounts", False)],
    ids=["failed-without-kind", "completed-with-kind", "running-with-kind"],
)
def test_failure_kind_exactly_when_failed(db_conn, status, failure_kind, finished):
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            """
            INSERT INTO billing_run
                (run_date, status, started_at, finished_at, trigger, failure_kind)
            VALUES (%s, %s, %s, %s, 'scheduled', %s)
            """,
            (D, status, NOW, NOW if finished else None, failure_kind),
        )


def test_attempt_is_positive(db_conn, run):
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_run SET attempt = 0 WHERE id = %s", (run["id"],))


# --- billing_run_account ---


def test_valid_outcomes(db_conn, run):
    later = new_run(db_conn, D + timedelta(days=1))
    a, b, c = account(db_conn, name="A"), account(db_conn, name="B"), account(db_conn, name="C")
    outcome(db_conn, run["id"], a["id"], "ok")
    outcome(db_conn, run["id"], b["id"], "failed", error="MissingPrice", attempts=2)
    outcome(db_conn, later["id"], b["id"], "blocked", waits_for=run["id"])
    outcome(db_conn, later["id"], c["id"], "ok")
    count = db_conn.execute("SELECT count(*) FROM billing_run_account").fetchone()[0]
    assert count == 4


@pytest.mark.parametrize(
    ("status", "error", "waits", "attempts"),
    [
        ("failed", None, False, 1),  # a failure says why
        ("ok", "boom", False, 1),  # only a failure has an error
        ("blocked", None, False, 1),  # a blocked row names the run it waits for
        ("ok", None, True, 1),  # only a blocked row waits
        ("ok", None, False, 0),  # attempts start at 1
    ],
    ids=["failed-no-error", "ok-with-error", "blocked-no-wait", "ok-waits", "zero-attempts"],
)
def test_outcome_constraints(db_conn, run, status, error, waits, attempts):
    other = new_run(db_conn, D - timedelta(days=1))
    acc = account(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        outcome(
            db_conn,
            run["id"],
            acc["id"],
            status,
            error=error,
            waits_for=other["id"] if waits else None,
            attempts=attempts,
        )


def test_a_blocked_row_cannot_wait_for_its_own_run(db_conn, run):
    acc = account(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        outcome(db_conn, run["id"], acc["id"], "blocked", waits_for=run["id"])


def test_one_outcome_per_run_and_account(db_conn, run):
    acc = account(db_conn)
    outcome(db_conn, run["id"], acc["id"], "ok")
    with rejected(db_conn, errors.UniqueViolation):
        outcome(db_conn, run["id"], acc["id"], "ok")


def test_outcome_references_a_real_account_and_run(db_conn, run):
    acc = account(db_conn)
    with rejected(db_conn, errors.ForeignKeyViolation):
        outcome(db_conn, run["id"], uuid4(), "ok")
    with rejected(db_conn, errors.ForeignKeyViolation):
        outcome(db_conn, uuid4(), acc["id"], "ok")
    with rejected(db_conn, errors.ForeignKeyViolation):
        outcome(db_conn, run["id"], acc["id"], "blocked", waits_for=uuid4())


# --- worker_request ---


def test_valid_requests(db_conn, run):
    request(db_conn, "catchUp", up_to=D)
    request(db_conn, "retry", run_id=run["id"], picked=NOW, done=NOW + timedelta(seconds=5))
    count = db_conn.execute("SELECT count(*) FROM worker_request").fetchone()[0]
    assert count == 2


@pytest.mark.parametrize(
    ("kind", "up_to", "with_run", "picked", "done"),
    [
        ("catchUp", None, False, None, None),  # a catch-up names its date
        ("catchUp", D, True, None, None),  # ... and no run
        ("retry", None, False, None, None),  # a retry names its run
        ("retry", D, True, None, None),  # ... and no date
        ("catchUp", D, False, NOW - timedelta(seconds=1), None),  # picked before requested
        ("catchUp", D, False, None, NOW),  # done without being picked
        ("catchUp", D, False, NOW + timedelta(seconds=5), NOW + timedelta(seconds=1)),
    ],
    ids=[
        "catch-up-no-date",
        "catch-up-with-run",
        "retry-no-run",
        "retry-with-date",
        "picked-before-requested",
        "done-not-picked",
        "done-before-picked",
    ],
)
def test_request_constraints(db_conn, run, kind, up_to, with_run, picked, done):
    with rejected(db_conn, errors.CheckViolation):
        request(
            db_conn,
            kind,
            up_to=up_to,
            run_id=run["id"] if with_run else None,
            picked=picked,
            done=done,
        )


# --- billing_account.opened_at ---


def test_opened_at_is_required(db_conn):
    acc = account(db_conn)
    with rejected(db_conn, errors.NotNullViolation):
        db_conn.execute(
            """
            INSERT INTO billing_account (customer_id, name, state, currency, bill_cycle_day)
            VALUES (%s, 'x', 'Active', 'USD', 1)
            """,
            (acc["customer_id"],),
        )
