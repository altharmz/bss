"""Starting the billing run of a date (app/worker/runs.py) and the worker's connection.

Every row of docs/stage4-design.md section 2, on real connections: ownership is the
session advisory lock, so "the owner is gone" is a closed connection and "the owner is
alive" is a second connection that still holds it.
"""

from datetime import date, datetime, timedelta

import pytest

from app.repository import billing
from app.worker import db as worker_db
from app.worker.runs import AlreadyCompleted, RunInProgress, Started, start_run
from tests.factories import MSK

D = date(2026, 2, 1)
NOW = datetime(2026, 2, 1, 0, 30, tzinfo=MSK)
LATER = NOW + timedelta(hours=2)


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def connect(migrated_db_url):
    """Extra worker connections, closed at the end of the test."""
    opened = []

    def open_one():
        conn = worker_db.connect(migrated_db_url)
        opened.append(conn)
        return conn

    yield open_one
    for conn in opened:
        conn.close()


def runs(conn) -> list[tuple]:
    return conn.execute(
        "SELECT run_date, status, attempt FROM billing_run ORDER BY run_date"
    ).fetchall()


# --- section 2, row by row ---


@pytest.mark.capability("worker.runLifecycle")
def test_no_run_yet_starts_one(worker):
    result = start_run(worker, D, now=NOW, trigger="scheduled")
    assert isinstance(result, Started) and result.how == "new"
    run = result.run
    assert (run["run_date"], run["status"], run["attempt"], run["trigger"]) == (
        D,
        "running",
        1,
        "scheduled",
    )
    assert run["started_at"] == NOW
    assert worker_db.holds_ownership(worker)


@pytest.mark.capability("worker.runLifecycle")
def test_a_completed_run_is_a_noop_not_an_error(worker):
    run = billing.insert_run(worker, D, now=NOW, trigger="scheduled")
    done = billing.finish_run(worker, run["id"], now=NOW + timedelta(minutes=5))
    result = start_run(worker, D, now=LATER, trigger="catchUp")
    assert isinstance(result, AlreadyCompleted)
    assert result.run == done
    assert billing.get_run(worker, D) == done  # nothing changed


@pytest.mark.capability("worker.runLifecycle")
def test_a_failed_run_is_reopened(worker):
    run = billing.insert_run(worker, D, now=NOW, trigger="scheduled")
    billing.fail_run(worker, run["id"], now=NOW + timedelta(minutes=5), error="db down")
    result = start_run(worker, D, now=LATER, trigger="retry")
    assert isinstance(result, Started) and result.how == "reopened"
    again = result.run
    assert again["id"] == run["id"]
    assert (again["status"], again["attempt"], again["trigger"]) == ("running", 2, "retry")
    assert (again["error"], again["failure_kind"], again["finished_at"]) == (None, None, None)


@pytest.mark.capability("worker.runLifecycle")
def test_a_running_run_whose_owner_is_gone_is_taken_over(worker, connect):
    first = connect()
    assert start_run(first, D, now=NOW, trigger="catchUp").how == "new"
    first.close()  # the owner dies: its session lock goes with it
    result = start_run(worker, D, now=LATER, trigger="scheduled")
    assert isinstance(result, Started) and result.how == "takenOver"
    run = result.run
    assert (run["status"], run["attempt"], run["trigger"]) == ("running", 2, "catchUp")
    assert (run["started_at"], run["heartbeat_at"]) == (NOW, LATER)


@pytest.mark.capability("worker.doubleStart")
def test_a_second_worker_backs_off_and_writes_nothing(worker, connect):
    assert start_run(worker, D, now=NOW, trigger="scheduled").how == "new"
    second = connect()
    with pytest.raises(RunInProgress):
        start_run(second, D + timedelta(days=1), now=NOW, trigger="scheduled")
    with pytest.raises(RunInProgress):
        start_run(second, D, now=NOW, trigger="scheduled")  # not even the running date
    assert not worker_db.holds_ownership(second)
    assert runs(worker) == [(D, "running", 1)]  # the second wrote nothing


@pytest.mark.capability("worker.doubleStart")
def test_ownership_is_released_explicitly_too(worker, connect):
    start_run(worker, D, now=NOW, trigger="scheduled")
    worker_db.release_ownership(worker)
    second = connect()
    assert start_run(second, D, now=LATER, trigger="scheduled").how == "takenOver"


# --- W1: the worker's connection ---


def test_the_worker_connection_settings(worker):
    assert worker.autocommit is True
    assert worker.execute("SHOW lock_timeout").fetchone() == ("30s",)
    assert worker.execute("SHOW application_name").fetchone() == ("bss-worker",)
    dsn = worker.info.dsn
    for option in (
        "connect_timeout=10",
        "keepalives=1",
        "keepalives_idle=30",
        "keepalives_interval=10",
        "keepalives_count=3",
    ):
        assert option in dsn.split(), f"{option} missing in {dsn!r}"
