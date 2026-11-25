"""The real worker process (python -m app.worker): --once, the start checks, and what
SIGTERM and SIGKILL do in the middle of a run (D-W11, D-W13, Q14).

The process runs on a pinned clock (WORKER_FAKE_NOW, honoured only with APP_ENV=test) so
no test depends on the real time of day. To catch the worker mid-account, the test holds
the row lock of the second account (FOR UPDATE): the worker waits inside that account's
transaction until the test releases it.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import date

import psycopg
import pytest

from app.repository import billing
from tests.builders.worker.worker_failures import accounts_with_products, advisory_locks
from tests.builders.worker.worker_process import DEC1

PINNED = "2026-12-01T00:30:00+03:00"  # the run of Dec 1 is due


def start(url: str, tmp_path, *args: str, **env: str) -> subprocess.Popen:
    environment = {
        **os.environ,
        "DATABASE_URL": url,
        "APP_ENV": "test",
        "WORKER_FAKE_NOW": PINNED,
        "WORKER_LIVENESS_FILE": str(tmp_path / "alive"),
        "LOG_LEVEL": "info",
        **env,
    }
    return subprocess.Popen(  # noqa: S603 - our own interpreter and module, no outside input
        [sys.executable, "-m", "app.worker", *args],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def finish(process: subprocess.Popen, timeout: float = 60) -> tuple[int, str]:
    output, _ = process.communicate(timeout=timeout)
    return process.returncode, output


WORKER_BLOCK_DEADLINE = 60  # seconds, the same as finish's timeout

WORKER_SESSIONS = (
    "SELECT state, wait_event_type, wait_event, left(query, 200) AS query"
    " FROM pg_stat_activity WHERE datname = current_database()"
    " AND application_name = 'bss-worker'"
)


def wait_until_blocked(
    url: str, process: subprocess.Popen, deadline: float = WORKER_BLOCK_DEADLINE
) -> None:
    """Until the worker's session waits for a row lock (it is inside an account).

    Ends as soon as the worker blocks; a loaded host (other slots' test runs) can take
    well over 10 s to start the process (#270). On timeout the message tells start-up
    (no session) from ordering (a session doing something else).
    """
    ends = time.monotonic() + deadline
    with psycopg.connect(url, autocommit=True) as admin:
        while True:
            sessions = admin.execute(WORKER_SESSIONS).fetchall()
            if any(wait_event_type == "Lock" for _, wait_event_type, _, _ in sessions):
                return
            assert process.poll() is None, finish(process)[1]
            if time.monotonic() >= ends:
                break
            time.sleep(0.1)
    state = "alive" if process.poll() is None else f"exited with {process.returncode}"
    process.kill()  # to read its output so far
    output = finish(process, timeout=10)[1]
    pytest.fail(
        f"the worker never waited for the held account within {deadline} s"
        f"\nprocess: {state}\nbss-worker sessions: {sessions or 'none'}"
        f"\noutput so far:\n{output}"
    )


def outcomes(url: str, day: date) -> tuple[dict, str]:
    with psycopg.connect(url, autocommit=True) as conn:
        run = billing.get_run(conn, day)
        return billing.outcomes(conn, run["id"]), run["status"]


# --- --once and the start checks ---


@pytest.mark.capability("worker.scheduler")
def test_once_bills_the_due_date_and_exits(api, migrated_db_url, tmp_path):
    accounts_with_products(api)
    code, output = finish(start(migrated_db_url, tmp_path, "--once"))
    assert code == 0, output
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        run = billing.get_run(conn, DEC1)
    assert (run["status"], run["trigger"]) == ("completed", "scheduled")
    assert (tmp_path / "alive").exists()


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"APP_ENV": "dev"}, "tests only"),  # Q14: the pinned clock outside APP_ENV=test
        ({"APP_ENV": "prod"}, "tests only"),
        ({"BILLING_RUN_AT": "03:00"}, "strictly between 00:00 and 02:00"),  # D-W13
    ],
)
def test_the_worker_refuses_to_start(api, migrated_db_url, tmp_path, env, reason):
    code, output = finish(start(migrated_db_url, tmp_path, "--once", **env))
    assert code == 2, output
    assert "refusing to start" in output and reason in output
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        assert billing.latest_run_date(conn) is None  # nothing ran


# --- signals mid-run ---


def test_wait_until_blocked_fails_with_diagnostics_within_its_deadline(migrated_db_url, tmp_path):
    # The child prints, then marks itself ready; the 0.5 s deadline starts only then, so a
    # slow interpreter start (coverage, a loaded host) cannot lose the output (#480).
    ready = tmp_path / "ready"
    child = (
        "import pathlib, sys, time; print('booting', flush=True); "
        "pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
    )
    never_blocks = subprocess.Popen(  # noqa: S603 - our own interpreter, no outside input
        [sys.executable, "-c", child, str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        start_by = time.monotonic() + 60
        while not ready.exists():
            assert never_blocks.poll() is None, "child exited before it was ready"
            assert time.monotonic() < start_by, "child never became ready"
            time.sleep(0.02)
        began = time.monotonic()
        with pytest.raises(pytest.fail.Exception) as failure:
            wait_until_blocked(migrated_db_url, never_blocks, deadline=0.5)
    finally:
        if never_blocks.poll() is None:
            never_blocks.kill()
    assert time.monotonic() - began < 10  # the deadline, not the 60 s default
    message = str(failure.value)
    assert "within 0.5 s" in message
    assert "process: alive" in message
    assert "bss-worker sessions: none" in message  # never connected: start-up
    assert "booting" in message  # its output so far


@pytest.mark.capability("worker.signals")
def test_sigterm_finishes_the_account_in_hand_and_stops(api, migrated_db_url, tmp_path):
    first, held, last = accounts_with_products(api)
    holder = psycopg.connect(migrated_db_url)
    holder.execute("SELECT id FROM billing_account WHERE id = %s FOR UPDATE", (held,))
    process = start(migrated_db_url, tmp_path)  # the loop, not --once
    try:
        wait_until_blocked(migrated_db_url, process)
        process.send_signal(signal.SIGTERM)
        time.sleep(0.5)  # the signal is in; the account is still blocked
        holder.rollback()  # release: the worker finishes the account in hand
        code, output = finish(process)
    finally:
        holder.close()
        if process.poll() is None:
            process.kill()
    assert code == 0, output
    rows, status = outcomes(migrated_db_url, DEC1)
    assert (rows[first]["status"], rows[held]["status"]) == ("ok", "ok")  # finished
    assert last not in rows  # no new account after the signal
    assert status == "running"  # left for the next worker, not failed
    with psycopg.connect(migrated_db_url, autocommit=True) as admin:
        assert advisory_locks(admin) == 0  # the lock went with the connection


@pytest.mark.capability("worker.signals")
def test_sigkill_leaves_nothing_half_written_and_the_next_worker_takes_over(
    api, migrated_db_url, tmp_path
):
    first, held, last = accounts_with_products(api)
    holder = psycopg.connect(migrated_db_url)
    holder.execute("SELECT id FROM billing_account WHERE id = %s FOR UPDATE", (held,))
    process = start(migrated_db_url, tmp_path)
    try:
        wait_until_blocked(migrated_db_url, process)
        process.kill()  # SIGKILL: no handler runs
        assert process.wait(timeout=10) == -signal.SIGKILL
        holder.rollback()
    finally:
        holder.close()
    with psycopg.connect(migrated_db_url, autocommit=True) as admin:
        for _ in range(100):  # the server notices the dead client and frees its lock
            if advisory_locks(admin) == 0:
                break
            time.sleep(0.05)
        assert advisory_locks(admin) == 0
    rows, status = outcomes(migrated_db_url, DEC1)
    assert (set(rows), status) == ({first}, "running")  # the account in hand: nothing
    with psycopg.connect(migrated_db_url, autocommit=True) as admin:
        charged = admin.execute(
            "SELECT count(*) FROM applied_customer_billing_rate WHERE billing_account_id = %s",
            (held,),
        ).fetchone()[0]
    assert charged == 0

    code, output = finish(start(migrated_db_url, tmp_path, "--once"))  # the successor
    assert code == 0, output
    rows, status = outcomes(migrated_db_url, DEC1)
    assert status == "completed"
    assert rows[first]["attempts"] == 1  # untouched by the takeover
    assert {rows[a]["status"] for a in (held, last)} == {"ok"}
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        assert billing.get_run(conn, DEC1)["attempt"] == 2
