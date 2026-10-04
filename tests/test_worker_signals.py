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
from tests.test_worker_failures import accounts_with_products, advisory_locks
from tests.test_worker_process import DEC1

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


def wait_until_blocked(url: str, process: subprocess.Popen) -> None:
    """Until the worker's session waits for a row lock (it is inside an account)."""
    query = (
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        " AND application_name = 'bss-worker' AND wait_event_type = 'Lock'"
    )
    with psycopg.connect(url, autocommit=True) as admin:
        for _ in range(200):
            if admin.execute(query).fetchone()[0]:
                return
            assert process.poll() is None, finish(process)[1]
            time.sleep(0.05)
    pytest.fail("the worker never waited for the held account")


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
