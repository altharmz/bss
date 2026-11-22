"""What one account costs in a billing run (#283, docs/billing-worker.md "Capacity"): the
run is bound by round trips to the database, not by CPU, so the per-account path takes
the account's row lock once and reads the accounts' unresolved earlier failures once per
run, not once per account."""

import re
from datetime import date

import pytest

from app.repository import billing
from app.worker import db as worker_db
from app.worker.process import DateRun
from app.worker.runs import start_run
from tests.builders.worker_process import DEC1, at, outcome, scenario
from tests.builders.worker_run_cost import DEC4
from tests.querycount import counting
from tests.timeline import TZ

# a plain statement, or a sql.Composed one (select_by_id), as tests/querycount records it
ACCOUNT_LOCK = re.compile(
    r"FROM (billing_account|.*Identifier\('billing_account'\)).*WHERE id = .*FOR UPDATE", re.S
)
FAILURE_LOOKUP = re.compile(r"o\.status = 'failed'")


def test_the_lock_pattern_sees_both_forms():
    from psycopg import sql

    composed = sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier("billing_account"))
    assert ACCOUNT_LOCK.search(str(composed + sql.SQL(" FOR UPDATE")))
    assert ACCOUNT_LOCK.search("SELECT id FROM billing_account WHERE id = %s FOR UPDATE")
    assert not ACCOUNT_LOCK.search("SELECT id FROM billing_account WHERE id = %s")


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def test_an_account_is_locked_once_and_failures_are_read_once_per_run(api, worker, monkeypatch):
    """x failed on Dec 1 (and is blocked on Dec 2 and 3); the run of Dec 4 bills a and b
    and blocks x on Dec 1 - with one lock per account and no failure lookup per account."""
    s = scenario(api, worker, broken=True)
    a, b, x = s["ids"]["a"], s["ids"]["b"], s["ids"]["x"]
    api.clock.set(at(DEC4, 0, 30))
    started = start_run(worker, DEC4, now=api.clock.now(), trigger="scheduled", tz=TZ.key)
    with counting(monkeypatch) as setup:
        date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    with counting(monkeypatch) as seen:
        date_run.run_all()
    date_run.close()

    assert sum(bool(FAILURE_LOOKUP.search(q)) for q in setup) == 1  # once, for the run
    assert not [q for q in seen if FAILURE_LOOKUP.search(q)]
    assert sum(bool(ACCOUNT_LOCK.search(q)) for q in seen) == 3  # one per account
    # the results are those of the per-account path
    dec1 = billing.get_run(worker, DEC1)
    assert [outcome(worker, DEC4, i)["status"] for i in (a, b)] == ["ok", "ok"]
    blocked = outcome(worker, DEC4, x)
    assert (blocked["status"], blocked["waits_for_run_id"]) == ("blocked", dec1["id"])


def test_the_failures_read_for_a_run_are_each_accounts_earliest(api, worker):
    """Two failed dates for x: its blocked rows wait for the earlier one; an account
    without a failure, or failing only on the run date or later, is not in the map."""
    s = scenario(api, worker, broken=True)
    x = s["ids"]["x"]
    dec1, dec2 = billing.get_run(worker, DEC1), s["runs"][date(2026, 12, 2)]
    with worker.transaction():
        worker.execute(
            "UPDATE billing_run_account SET status = 'failed', waits_for_run_id = NULL,"
            " error = 'X: later' WHERE run_id = %s AND billing_account_id = %s",
            (dec2["id"], x),
        )
    failures = billing.earliest_failures_before(worker, DEC4)
    assert set(failures) == {x}
    assert (failures[x]["run_id"], failures[x]["run_date"]) == (dec1["id"], DEC1)
    assert billing.earliest_failures_before(worker, DEC1) == {}
