"""The heartbeat keeps the worker's liveness fresh through a long run, in every path that
bills accounts (app/worker/process.py, DateRun.step; slice f's healthcheck).

The container is unhealthy once /tmp/worker-alive is 180 s old, so a run whose accounts
take longer than that in total must touch it between accounts - retries included, which
have no stop checkpoint. Scaled down in time: each account takes SLOW seconds (bill_account
wrapped with a sleep), the threshold is THRESHOLD, and every run here takes longer than
the threshold in total. The gaps between the start, every heartbeat and the end must all
stay under it, and there is exactly one heartbeat per account processed.

Time here is virtual: a slow account advances a test clock instead of sleeping, and the
heartbeat reads that clock, so host load cannot stretch a gap past the threshold and the
tests take no real time.
"""

from datetime import timedelta

import pytest

from app.repository import billing
from app.worker import db as worker_db
from app.worker import process
from app.worker.loop import tick
from app.worker.process import RunAborted, bill_date
from app.worker.queue import process_requests
from tests.builders.worker.worker_failures import accounts_with_products, fail_for
from tests.builders.worker.worker_process import DEC1, DEC2, DEC3, at, scenario, set_currency
from tests.builders.worker.worker_schedule import RUN_AT, msk
from tests.timeline import TZ

THRESHOLD = 1.0  # stands for the healthcheck's 180 s
SLOW = 0.3  # one account's billing time, stretched

DEC4 = DEC3 + timedelta(days=1)
RUNS = "/internal/v1/billingRun"


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


class VirtualTime:
    """The test's clock: only a slow account moves it."""

    now = 0.0


@pytest.fixture(autouse=True)
def virtual_time():
    VirtualTime.now = 0.0


class Liveness:
    """Records when the heartbeat fired, between a start and an end."""

    def __init__(self) -> None:
        self.beats: list[float] = []

    def __call__(self) -> None:
        self.beats.append(VirtualTime.now)

    def __enter__(self) -> "Liveness":
        self.start = VirtualTime.now
        return self

    def __exit__(self, *exc) -> None:
        self.end = VirtualTime.now

    def gaps(self) -> list[float]:
        points = [self.start, *self.beats, self.end]
        return [b - a for a, b in zip(points, points[1:], strict=False)]

    def assert_fresh(self, accounts: int) -> None:
        assert len(self.beats) == accounts  # one heartbeat per account processed
        assert self.end - self.start > THRESHOLD  # the run is longer than the threshold
        assert max(self.gaps()) < THRESHOLD, self.gaps()


def slow_accounts(monkeypatch) -> None:
    real = process.bill_account

    def slow(conn, account_id, run, **kw):
        VirtualTime.now += SLOW
        return real(conn, account_id, run, **kw)

    monkeypatch.setattr(process, "bill_account", slow)


def wake(conn, clock, heartbeat):
    return tick(conn, clock=clock, tz=TZ, run_at=RUN_AT, heartbeat=heartbeat)


def test_a_scheduled_run_keeps_the_worker_alive(api, worker, monkeypatch):
    accounts_with_products(api, n=5)
    slow_accounts(monkeypatch)
    api.clock.set(msk(DEC1, 0, 30))
    with Liveness() as alive:
        assert wake(worker, api.clock, alive).billed == [DEC1]
    alive.assert_fresh(accounts=5)


def test_a_retry_of_failed_accounts_keeps_the_worker_alive(api, worker, monkeypatch):
    """D-W17 through the wake-up: x is retried for Dec 1 and caught up on its blocked
    Dec 2 and 3 (retry_run, no checkpoint), then Dec 4 is billed for all three."""
    s = scenario(api, worker, broken=True)
    set_currency(worker, s["fragile"]["id"], "USD")
    slow_accounts(monkeypatch)
    api.clock.set(msk(DEC4, 0, 30))
    with Liveness() as alive:
        result = wake(worker, api.clock, alive)
    assert (result.retried, result.billed) == ([DEC1], [DEC4])
    alive.assert_fresh(accounts=3 + 3)


def test_a_queued_catch_up_keeps_the_worker_alive(api, worker, monkeypatch):
    accounts_with_products(api, n=3)
    api.clock.set(at(DEC1, 0, 30))
    bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    api.clock.set(at(DEC3, 12))
    assert api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"}).status_code == 202
    slow_accounts(monkeypatch)
    with Liveness() as alive:
        assert len(process_requests(worker, clock=api.clock, tz=TZ, heartbeat=alive)) == 1
    assert [billing.get_run(worker, d)["status"] for d in (DEC2, DEC3)] == ["completed"] * 2
    alive.assert_fresh(accounts=2 * 3)


def test_a_queued_retry_of_a_whole_run_failure_keeps_the_worker_alive(api, worker, monkeypatch):
    """The first account is billed, the second aborts the run; the retry bills the other
    four (bill_date with the retry trigger, no checkpoint)."""
    _, second, *_ = accounts_with_products(api, n=5)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    restore()
    api.post(f"{RUNS}/{billing.get_run(worker, DEC1)['id']}/retry", None)
    slow_accounts(monkeypatch)
    with Liveness() as alive:
        process_requests(worker, clock=api.clock, tz=TZ, heartbeat=alive)
    assert billing.get_run(worker, DEC1)["status"] == "completed"
    alive.assert_fresh(accounts=4)
