"""Stored member and outcome counts on billing_run (#284, docs/decisions/worker.md "Stored
run counts"): member_count is written when a run starts, the outcome counts in the
transaction of each outcome row, and the runs view reads only the stored columns. Each
scenario ends with the live recount (tests/run_counts.py)."""

import re
import uuid
from datetime import date

import psycopg
import pytest

from app.billing.model import MissingPrice
from app.repository import billing
from app.worker import db as worker_db
from app.worker import process
from app.worker.process import DateRun, bill_date, retry_run
from app.worker.runs import start_run
from tests import apidata, timeline
from tests.builders.worker_process import DEC1, DEC2, DEC3, NOV20, add, at, scenario, set_currency
from tests.factories import account, run
from tests.querycount import counting
from tests.run_counts import assert_consistent
from tests.timeline import TZ

DEC4 = date(2026, 12, 4)
RUN_WRITE = re.compile(r"\bUPDATE billing_run\b(?!_)")


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.capability("worker.storedRunCounts")
def test_counts_after_an_accounts_failure_a_retry_and_the_release_of_blocked_accounts(api, worker):
    s = scenario(api, worker, broken=True)  # x fails on Dec 1, is blocked on Dec 2 and 3
    assert assert_consistent(worker) == {
        DEC1: (3, 2, 1, 0),
        DEC2: (3, 2, 0, 1),
        DEC3: (3, 2, 0, 1),
    }
    set_currency(worker, s["fragile"]["id"], "USD")
    api.clock.set(at(DEC3, 12))
    retry_run(worker, s["runs"][DEC1]["id"], clock=api.clock, tz=TZ)  # failed -> ok, blocked -> ok
    assert assert_consistent(worker) == {day: (3, 3, 0, 0) for day in (DEC1, DEC2, DEC3)}


@pytest.mark.capability("worker.storedRunCounts")
def test_counts_after_clean_runs(api, worker):
    scenario(api, worker, broken=False)
    assert assert_consistent(worker) == {day: (3, 3, 0, 0) for day in (DEC1, DEC2, DEC3)}


@pytest.mark.capability("worker.storedRunCounts")
def test_counts_after_a_takeover_of_a_killed_worker(api, worker, migrated_db_url):
    """The first worker's backend is terminated after its first account; the run stays
    running and a second worker takes it over: the counts are the same as a clean run's."""

    def killed_then_taken_over(conn, clock, ids):
        def kill():
            with psycopg.connect(migrated_db_url, autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(%s, 5000)", (conn.info.backend_pid,))
            return False

        clock.set(at(DEC1, 0, 30))
        with pytest.raises(process.RunAborted) as aborted:
            bill_date(conn, DEC1, clock=clock, tz=TZ, trigger="scheduled", checkpoint=kill)
        assert aborted.value.marked is False
        second = worker_db.connect(migrated_db_url)
        partial = assert_consistent(second)
        assert partial == {DEC1: (3, 1, 0, 0)}  # one account billed before the kill
        clock.set(at(DEC1, 3, 1))
        taken = bill_date(second, DEC1, clock=clock, tz=TZ, trigger="scheduled")
        assert (taken["status"], taken["attempt"]) == ("completed", 2)
        return second

    s = scenario(api, worker, broken=False, first_run=killed_then_taken_over)
    try:
        assert assert_consistent(s["conn"]) == {day: (3, 3, 0, 0) for day in (DEC1, DEC2, DEC3)}
    finally:
        s["conn"].close()


@pytest.mark.capability("worker.storedRunCounts")
@pytest.mark.parametrize("when", ["before the members are listed", "before its turn"])
def test_a_member_deleted_during_the_run_leaves_the_member_count(api, worker, when):
    """An account without history can be deleted while a run is in flight; the delete takes
    it out of member_count in its own transaction, so the counts stay right whether the
    worker never saw it or skips it as gone."""
    api.clock.set(timeline.NOW)
    offering = apidata.sellable_offering(api, value=300)
    api.clock.set(at(NOV20, 9))
    kept = apidata.account(api)
    add(api, kept, offering, DEC1, on=at(NOV20))
    doomed = apidata.account(api)  # nothing ordered: deletable
    api.clock.set(at(DEC1, 0, 30))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled", tz=TZ.key)
    assert started.run["member_count"] == 2
    if when == "before the members are listed":
        assert api.delete(doomed["href"]).status_code == 204
        date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    else:
        date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
        assert len(date_run.todo) == 2
        assert api.delete(doomed["href"]).status_code == 204
    date_run.run_all()
    assert date_run.close()["status"] == "completed"
    assert assert_consistent(worker) == {DEC1: (1, 1, 0, 0)}


@pytest.mark.capability("worker.storedRunCounts")
def test_one_billing_run_write_per_processed_account(api, worker, monkeypatch):
    """The outcome row, the counts and the heartbeat: one billing_run UPDATE per account,
    for each outcome - ok, failed and blocked."""
    s = scenario(api, worker, broken=True)  # x has an unresolved failure on Dec 1
    a, b, x = s["ids"]["a"], s["ids"]["b"], s["ids"]["x"]
    real = process.bill_account

    def a_fails(conn, account_id, run, **kw):
        if account_id == a:
            raise MissingPrice(uuid.uuid4(), DEC4, "USD")
        return real(conn, account_id, run, **kw)

    monkeypatch.setattr(process, "bill_account", a_fails)
    api.clock.set(at(DEC4, 0, 30))
    started = start_run(worker, DEC4, now=api.clock.now(), trigger="scheduled", tz=TZ.key)
    date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    writes = {}
    for account_id in list(date_run.todo):
        with counting(monkeypatch) as seen:
            assert date_run.step()
        writes[account_id] = sum(1 for q in seen if RUN_WRITE.search(q))
    statuses = {i: r["status"] for i, r in billing.outcomes(worker, started.run["id"]).items()}
    assert statuses == {a: "failed", b: "ok", x: "blocked"}
    assert writes == {a: 1, b: 1, x: 1}
    date_run.close()
    assert assert_consistent(worker)[DEC4] == (3, 1, 1, 1)


def test_the_runs_view_reads_only_the_stored_counts(db_conn):
    """Stored values the rows could never produce come back as they are: nothing is
    recounted."""
    account(db_conn)
    stored = run(db_conn, date(2026, 2, 1))
    db_conn.execute(
        "UPDATE billing_run SET member_count = 7, ok_count = 4, failed_count = 2,"
        " blocked_count = 1 WHERE id = %s",
        (stored["id"],),
    )
    listed, more = billing.page_runs(db_conn, offset=0, limit=20)
    one = billing.run_view(db_conn, stored["id"])
    for row in (listed[0], one):
        assert (row["total"], row["ok"], row["failed"], row["blocked"]) == (7, 4, 2, 1)
    assert more is False
