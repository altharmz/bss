"""Whole-run failures and takeovers (app/worker/process.py, app/worker/plan.py).

A whole-run failure - anything but an account's BillingError - is not an account's
problem: the run fails as a whole (failure_kind 'run'), no later date is billed until it
succeeds, and the scheduled retry waits 5 minutes, then 15, then hourly. A worker that
dies cannot mark anything: its run stays running, its lock dies with its connection, and
the next worker takes the run over without touching the accounts already ok.
"""

from datetime import datetime, timedelta

import psycopg
import pytest

from app.repository import billing
from app.worker import db as worker_db
from app.worker.plan import BACKOFF, retry_time
from app.worker.process import DateRun, HeldBack, NotYet, RunAborted, bill_date
from app.worker.runs import RunInProgress, start_run
from tests.builders.worker_failures import accounts_with_products, advisory_locks, fail_for
from tests.builders.worker_process import DEC1, DEC2, at
from tests.factories import MSK
from tests.timeline import TZ


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        if not conn.closed:
            conn.close()


def charges(conn, account_id) -> int:
    return conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE billing_account_id = %s",
        (account_id,),
    ).fetchone()[0]


def outcome(conn, day, account_id):
    return billing.outcomes(conn, billing.get_run(conn, day)["id"]).get(account_id)


def terminate(admin, pid: int) -> bool:
    """Kill a session and wait (up to 5 s) until it has exited - pg_terminate_backend
    without a timeout returns before the backend is gone."""
    return admin.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,)).fetchone()[0]


# --- the backoff ---


@pytest.mark.capability("worker.wholeRunFailure")
@pytest.mark.parametrize(("attempt", "wait"), [(1, 5), (2, 15), (3, 60), (7, 60)])
def test_the_backoff_is_5_15_then_hourly(attempt, wait):
    finished = datetime(2026, 12, 1, 0, 31, tzinfo=MSK)
    run = {
        "run_date": DEC1,
        "status": "failed",
        "failure_kind": "run",
        "attempt": attempt,
        "finished_at": finished,
    }
    assert retry_time(run) == finished + timedelta(minutes=wait)
    assert BACKOFF[-1] == timedelta(hours=1)


def test_only_a_run_that_failed_as_a_whole_has_a_retry_time():
    run = {
        "run_date": DEC1,
        "status": "failed",
        "failure_kind": "accounts",
        "attempt": 1,
        "finished_at": datetime(2026, 12, 1, 0, 31, tzinfo=MSK),
    }
    with pytest.raises(ValueError, match="did not fail as a whole"):
        retry_time(run)


# --- a whole-run failure does not advance ---


@pytest.mark.capability("worker.wholeRunFailure")
def test_a_run_level_error_fails_the_run_and_later_dates_wait(api, worker, monkeypatch):
    first, second, third = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted) as aborted:
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    assert aborted.value.marked is True
    run = billing.get_run(worker, DEC1)
    assert (run["status"], run["failure_kind"]) == ("failed", "run")
    assert run["error"] == "RuntimeError: injected bug"
    # the account before it is billed and stays so; it and the rest have nothing
    assert outcome(worker, DEC1, first)["status"] == "ok"
    assert [outcome(worker, DEC1, a) for a in (second, third)] == [None, None]
    assert [charges(worker, a) for a in (first, second, third)] == [1, 0, 0]
    # no later date while Dec 1 failed as a whole - not even with the fault gone
    restore()
    api.clock.set(at(DEC2, 0, 30))
    with pytest.raises(HeldBack) as held:
        bill_date(worker, DEC2, clock=api.clock, tz=TZ, trigger="scheduled")
    assert held.value.run["run_date"] == DEC1
    assert billing.get_run(worker, DEC2) is None


@pytest.mark.capability("worker.wholeRunFailure")
def test_the_scheduled_retry_waits_for_its_backoff_then_the_dates_go_on(api, worker, monkeypatch):
    first, second, third = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    restore()
    failed_at = billing.get_run(worker, DEC1)["finished_at"]
    api.clock.set(failed_at + timedelta(minutes=5) - timedelta(seconds=1))
    with pytest.raises(NotYet) as early:
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    assert early.value.due == failed_at + timedelta(minutes=5)
    assert billing.get_run(worker, DEC1)["attempt"] == 1  # untouched
    api.clock.set(failed_at + timedelta(minutes=5))
    done = bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    assert (done["status"], done["attempt"]) == ("completed", 2)
    assert outcome(worker, DEC1, first)["attempts"] == 1  # ok before: not billed again
    assert [charges(worker, a) for a in (first, second, third)] == [1, 1, 1]
    api.clock.set(at(DEC2, 0, 30))
    assert bill_date(worker, DEC2, clock=api.clock, tz=TZ, trigger="scheduled")["status"] == (
        "completed"
    )


@pytest.mark.capability("worker.wholeRunFailure")
def test_an_operator_retry_is_not_held_to_the_backoff(api, worker, monkeypatch):
    _, second, _ = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    restore()
    api.clock.set(at(DEC1, 0, 31))  # well within the 5 minutes
    done = bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="retry")
    assert (done["status"], done["attempt"], done["trigger"]) == ("completed", 2, "retry")


@pytest.mark.capability("worker.wholeRunFailure")
def test_a_second_failure_waits_longer(api, worker, monkeypatch):
    _, second, _ = accounts_with_products(api)
    fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    api.clock.set(billing.get_run(worker, DEC1)["finished_at"] + timedelta(minutes=5))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    run = billing.get_run(worker, DEC1)
    assert (run["attempt"], retry_time(run)) == (2, run["finished_at"] + timedelta(minutes=15))


# --- LockNotAvailable: a row lock held past lock_timeout ---


@pytest.mark.capability("worker.wholeRunFailure")
def test_a_lock_timeout_is_a_whole_run_failure(api, migrated_db_url):
    """Another transaction holds an account's row past lock_timeout (an order, a PATCH):
    transient contention, not the account's data - the run fails as a whole and is
    retried after the backoff; nothing is blocked, the account before it stays billed."""
    first, locked, last = accounts_with_products(api)
    worker = worker_db.connect(migrated_db_url, lock_timeout="300ms")
    holder = psycopg.connect(migrated_db_url)
    try:
        holder.execute("SELECT id FROM billing_account WHERE id = %s FOR UPDATE", (locked,))
        api.clock.set(at(DEC1, 0, 30))
        with pytest.raises(RunAborted) as aborted:
            bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        assert type(aborted.value.cause).__name__ == "LockNotAvailable"
        assert aborted.value.marked is True  # the connection survived the timeout
        run = billing.get_run(worker, DEC1)
        assert (run["status"], run["failure_kind"]) == ("failed", "run")
        assert run["error"].startswith("LockNotAvailable: ")
        assert outcome(worker, DEC1, first)["status"] == "ok"
        assert [outcome(worker, DEC1, a) for a in (locked, last)] == [None, None]
        assert [charges(worker, a) for a in (first, locked, last)] == [1, 0, 0]

        holder.rollback()  # the lock is released
        api.clock.set(run["finished_at"] + timedelta(minutes=5))
        done = bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (done["status"], done["attempt"]) == ("completed", 2)
        assert outcome(worker, DEC1, first)["attempts"] == 1
        assert [charges(worker, a) for a in (first, locked, last)] == [1, 1, 1]
    finally:
        holder.close()
        worker.close()


# --- takeover ---


@pytest.mark.capability("worker.takeover")
def test_a_takeover_after_a_death_between_accounts(api, worker, migrated_db_url):
    first, second, third = accounts_with_products(api)
    api.clock.set(at(DEC1, 0, 30))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    DateRun(worker, started.run, clock=api.clock, tz=TZ).step()  # one account, then death
    before = outcome(worker, DEC1, first)
    worker.close()

    successor = worker_db.connect(migrated_db_url)
    try:
        api.clock.set(at(DEC1, 3, 1))  # after the 03:00 reboot
        done = bill_date(successor, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (done["status"], done["attempt"]) == ("completed", 2)
        assert outcome(successor, DEC1, first) == before  # untouched: same row, attempts 1
        assert [charges(successor, a) for a in (first, second, third)] == [1, 1, 1]
    finally:
        successor.close()


@pytest.mark.capability("worker.takeover")
def test_a_dropped_connection_frees_the_lock_for_a_takeover(api, worker, migrated_db_url):
    first, _, _ = accounts_with_products(api)
    api.clock.set(at(DEC1, 0, 30))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    date_run.step()
    with psycopg.connect(migrated_db_url, autocommit=True) as admin:
        # waits (up to 5 s) until the backend is really gone
        assert terminate(admin, worker.info.backend_pid)
        with pytest.raises(psycopg.OperationalError):
            date_run.step()  # the next account: the session is gone
        assert billing.get_run(admin, DEC1)["status"] == "running"
        assert advisory_locks(admin) == 0  # the lock went with the session

    successor = worker_db.connect(migrated_db_url)
    try:
        done = bill_date(successor, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (done["status"], done["attempt"]) == ("completed", 2)
        assert outcome(successor, DEC1, first)["attempts"] == 1
    finally:
        successor.close()


@pytest.mark.capability("worker.takeover")
def test_no_takeover_while_the_owner_is_alive(api, worker, migrated_db_url):
    first, _, _ = accounts_with_products(api)
    api.clock.set(at(DEC1, 0, 30))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    DateRun(worker, started.run, clock=api.clock, tz=TZ).step()  # alive, mid-run
    rival = worker_db.connect(migrated_db_url)
    try:
        with pytest.raises(RunInProgress):
            bill_date(rival, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        run = billing.get_run(rival, DEC1)
        assert (run["status"], run["attempt"]) == ("running", 1)
        assert outcome(rival, DEC1, first)["attempts"] == 1
    finally:
        rival.close()
