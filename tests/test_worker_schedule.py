"""The worker's schedule (app/worker/plan.py scheduled, app/worker/loop.py tick; Q4, Q5,
D-W13, D-W15, D-W17): what one wake-up bills on its own, on a FixedClock around 00:00 and
00:30 in Europe/Moscow, against the real database."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.repository import billing
from app.worker import db as worker_db
from app.worker.loop import tick
from app.worker.plan import scheduled
from app.worker.runtime import StartRefused, check_run_at
from tests.test_worker_failures import accounts_with_products, fail_for
from tests.test_worker_process import DEC1, DEC2, DEC3, scenario, set_currency
from tests.timeline import TZ

NOV29, NOV30, DEC4 = date(2026, 11, 29), date(2026, 11, 30), date(2026, 12, 4)
RUN_AT = time(0, 30)


def msk(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=TZ)


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


# --- the pure decision ---


@pytest.mark.capability("worker.scheduler")
@pytest.mark.parametrize(
    ("latest", "held", "now", "dates", "gap"),
    [
        (DEC1, None, msk(DEC2, 0, 29, 59), (), None),  # Dec 2 is not due before 00:30
        (DEC1, None, msk(DEC2, 0, 30), (DEC2,), None),  # ... and is at 00:30
        (NOV30, None, msk(DEC2, 0, 30), (DEC1, DEC2), None),  # yesterday and today
        (NOV30, None, msk(DEC2, 0, 10), (DEC1,), None),  # before 00:30: up to yesterday
        (NOV29, None, msk(DEC2, 3, 1), (), (NOV30, NOV30)),  # an older gap: nothing
        (NOV29, None, msk(DEC2, 0, 10), (NOV30, DEC1), None),  # the window is from the due date
        (DEC1, DEC1, msk(DEC4, 0, 30), (DEC1,), None),  # failed as a whole: only its date
        (None, None, msk(DEC2, 0, 29), (DEC1,), None),  # first run ever: the due date only
        (None, None, msk(DEC2, 0, 30), (DEC2,), None),
        (DEC2, None, msk(DEC2, 23, 59), (), None),  # nothing due
    ],
)
def test_what_a_wake_up_bills_on_its_own(latest, held, now, dates, gap):
    plan = scheduled(latest, held, now, RUN_AT)
    assert (plan.dates, plan.gap) == (dates, gap)


@pytest.mark.capability("worker.scheduler")
@pytest.mark.parametrize(
    ("now", "dates"),
    [
        (msk(DEC1, 3, 1), (DEC1,)),  # killed at 03:00 on Dec 1: resumed at 03:01
        (msk(DEC2, 0, 30), (DEC1, DEC2)),  # down until the next run: resumed first
    ],
)
def test_a_run_left_running_is_resumed_first(now, dates):
    plan = scheduled(DEC1, None, now, RUN_AT, unfinished=DEC1)
    assert plan.dates == dates


@pytest.mark.capability("worker.scheduler")
def test_a_wake_up_resumes_a_run_left_running(api, worker, migrated_db_url):
    first, _, _ = accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    stopped = wake(worker, api.clock, checkpoint=lambda: True)  # left after one account
    assert stopped.stopped
    worker_db.release_ownership(worker)  # as if the process had exited
    successor = worker_db.connect(migrated_db_url)
    try:
        api.clock.set(msk(DEC1, 3, 1))
        assert wake(successor, api.clock).billed == [DEC1]
        run = billing.get_run(successor, DEC1)
        assert (run["status"], run["attempt"]) == ("completed", 2)
        assert billing.outcomes(successor, run["id"])[first]["attempts"] == 1
    finally:
        successor.close()


@pytest.mark.parametrize("value", ["00:00", "02:00", "23:00", "03:00"])
def test_the_worker_refuses_a_run_time_outside_its_window(value):
    with pytest.raises(StartRefused, match="strictly between 00:00 and 02:00"):
        check_run_at(value)


@pytest.mark.parametrize("value", ["00:01", "00:30", "01:59"])
def test_run_times_inside_the_window(value):
    assert check_run_at(value) == time.fromisoformat(value)


# --- one wake-up against the database ---


def wake(conn, clock, **kw):
    return tick(conn, clock=clock, tz=TZ, run_at=RUN_AT, **kw)


@pytest.mark.capability("worker.scheduler")
def test_the_run_of_a_date_starts_at_0030_not_at_midnight(api, worker):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    assert wake(worker, api.clock).billed == [DEC1]  # first start: the due date
    api.clock.set(msk(DEC2, 0, 29, 59))
    assert wake(worker, api.clock).billed == []
    api.clock.set(msk(DEC2, 0, 30))
    assert wake(worker, api.clock).billed == [DEC2]
    run = billing.get_run(worker, DEC2)
    assert (run["status"], run["trigger"]) == ("completed", "scheduled")
    assert wake(worker, api.clock).billed == []  # nothing new on the next wake-up


@pytest.mark.capability("worker.scheduler")
def test_the_clock_is_read_in_billing_tz(api, worker):
    """21:30 UTC on Dec 1 is 00:30 on Dec 2 in Moscow: the run of Dec 2 is due."""
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    wake(worker, api.clock)
    api.clock.set(datetime(2026, 12, 1, 21, 30, tzinfo=UTC))
    assert wake(worker, api.clock).billed == [DEC2]


@pytest.mark.capability("worker.scheduler")
def test_after_the_reboot_yesterday_and_today_are_caught_up_in_order(api, worker):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    wake(worker, api.clock)
    api.clock.set(msk(DEC3, 3, 1))  # down from Dec 1 00:45 until after Dec 3's 03:00 reboot
    assert wake(worker, api.clock).billed == [DEC2, DEC3]
    order = worker.execute("SELECT run_date FROM billing_run ORDER BY created_at, id").fetchall()
    assert order == [(DEC1,), (DEC2,), (DEC3,)]


@pytest.mark.capability("worker.scheduler")
def test_an_older_gap_is_reported_and_never_billed_silently(api, worker, caplog):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    wake(worker, api.clock)
    api.clock.set(msk(DEC4, 0, 30))  # Dec 2 lies outside the window (Dec 3, Dec 4)
    with caplog.at_level("WARNING", logger="app.worker"):
        result = wake(worker, api.clock)
    assert (result.billed, result.gap) == ([], (DEC2, DEC2))
    assert "catch up from the admin UI" in caplog.text
    assert billing.latest_run_date(worker) == DEC1  # nothing billed past the gap


@pytest.mark.capability("worker.scheduler")
def test_failed_accounts_are_retried_once_before_the_new_date(api, worker):
    """D-W17: x failed on Dec 1 and is blocked after; once fixed, the scheduled wake-up of
    Dec 4 retries Dec 1 (which catches x up on Dec 2 and 3) before billing Dec 4."""
    s = scenario(api, worker, broken=True)
    set_currency(worker, s["fragile"]["id"], "RUB")
    api.clock.set(msk(DEC4, 0, 30))
    result = wake(worker, api.clock)
    assert result.retried == [DEC1]  # Dec 2 and 3 were caught up by it
    assert result.billed == [DEC4]
    assert [billing.get_run(worker, d)["status"] for d in (DEC1, DEC2, DEC3, DEC4)] == [
        "completed"
    ] * 4
    again = wake(worker, api.clock)  # the same day again: no second retry
    assert (again.retried, again.billed) == ([], [])


@pytest.mark.capability("worker.scheduler")
def test_a_whole_run_failure_holds_the_schedule_until_its_retry(api, worker, monkeypatch):
    _, second, _ = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(msk(DEC1, 0, 30))
    assert wake(worker, api.clock).billed == []  # aborted
    restore()
    failed_at = billing.get_run(worker, DEC1)["finished_at"]
    api.clock.set(failed_at + timedelta(minutes=4))
    assert wake(worker, api.clock).billed == []  # inside the backoff: not yet
    api.clock.set(msk(DEC2, 0, 30))  # the next day: Dec 1, failed as a whole, comes first
    assert wake(worker, api.clock).billed == [DEC1]
    assert wake(worker, api.clock).billed == [DEC2]


@pytest.mark.capability("worker.scheduler")
def test_a_wake_up_processes_the_queue(api, worker):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 12))
    api.post("/internal/v1/billingRun/catchUp", {"upTo": "2026-12-01"})
    result = wake(worker, api.clock)
    assert (result.billed, len(result.requests)) == ([DEC1], 1)  # billed, then nothing queued
    assert billing.get_run(worker, DEC1)["trigger"] == "scheduled"


def test_a_second_worker_backs_off(api, worker, migrated_db_url):
    owner = worker_db.connect(migrated_db_url)
    try:
        assert worker_db.acquire_ownership(owner)
        assert wake(worker, api.clock).backed_off is True
    finally:
        owner.close()


def test_the_checkpoint_stops_between_accounts(api, worker):
    first, _, _ = accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    result = wake(worker, api.clock, checkpoint=lambda: True)  # stop after the first account
    assert result.stopped is True
    run = billing.get_run(worker, DEC1)
    assert run["status"] == "running"  # left for the next worker, not failed
    outcomes = billing.outcomes(worker, run["id"])
    assert list(outcomes) == [first] and outcomes[first]["status"] == "ok"
