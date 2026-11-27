"""The request queue's stop and failure paths (app/worker/queue.py, #511), against the real
test database: a catch-up that stops at a date failing as a whole, a retry of such a run that
fails again, and a stop inside a catch-up, which leaves the request to the next worker."""

import logging
from datetime import time
from uuid import UUID

import pytest

from app.repository import billing, worker_requests
from app.worker import db as worker_db
from app.worker.loop import tick
from app.worker.process import RunAborted, Stopped, bill_date
from app.worker.queue import process_requests
from tests.builders.worker.logs import worker_lines
from tests.builders.worker.worker_failures import accounts_with_products, fail_for
from tests.builders.worker.worker_process import DEC1, DEC2, DEC3, at
from tests.timeline import TZ

pytestmark = pytest.mark.capability("worker.requestQueue")

RUNS = "/internal/v1/billingRun"


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def runs(conn) -> list[tuple]:
    return conn.execute(
        "SELECT run_date, status::text, trigger::text, attempt FROM billing_run"
        " ORDER BY run_date, created_at"
    ).fetchall()


def test_a_catch_up_stops_at_a_date_that_fails_as_a_whole(api, worker, monkeypatch, caplog):
    _, second, _ = accounts_with_products(api)
    api.clock.set(at(DEC1, 0, 30))
    bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    fail_for(monkeypatch, second)
    api.clock.set(at(DEC3, 12))
    queued = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"}).json()
    assert queued["dates"] == ["2026-12-02", "2026-12-03"]
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        done = process_requests(worker, clock=api.clock, tz=TZ)
    request_id = UUID(queued["request"]["id"])
    assert done == [request_id]
    assert worker_requests.get_request(worker, request_id)["done_at"] is not None
    # Dec 2 failed as a whole; Dec 3 was never started
    assert runs(worker) == [(DEC1, "completed", "scheduled", 1), (DEC2, "failed", "catchUp", 1)]
    assert any(
        line.startswith("catch-up to 2026-12-03 stopped at 2026-12-02: ")
        for line in worker_lines(caplog, logging.WARNING)
    )
    failed = api.get(f"{RUNS}/{billing.get_run(worker, DEC2)['id']}").json()
    assert (failed["status"], failed["failureKind"]) == ("failed", "run")


def test_a_retry_of_a_run_failed_as_a_whole_that_fails_again_stays_failed(
    api, worker, monkeypatch, caplog
):
    _, second, _ = accounts_with_products(api)
    fail_for(monkeypatch, second)  # never fixed
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    run_id = billing.get_run(worker, DEC1)["id"]
    assert api.post(f"{RUNS}/{run_id}/retry", None).status_code == 202
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        assert len(process_requests(worker, clock=api.clock, tz=TZ)) == 1
    assert runs(worker) == [(DEC1, "failed", "retry", 2)]
    assert any(
        line.startswith("retry of 2026-12-01 failed again: ")
        for line in worker_lines(caplog, logging.WARNING)
    )
    shown = api.get(f"{RUNS}/{run_id}").json()
    assert (shown["status"], shown["failureKind"], shown["attempt"]) == ("failed", "run", 2)


def test_a_stop_inside_a_catch_up_leaves_the_request_to_the_next_worker(api, worker):
    accounts_with_products(api)
    api.clock.set(at(DEC1, 12))
    queued = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-01"}).json()
    request_id = UUID(queued["request"]["id"])
    answers = iter([False])  # go on to the request, then stop after its first account

    with pytest.raises(Stopped):
        process_requests(worker, clock=api.clock, tz=TZ, checkpoint=lambda: next(answers, True))
    request = worker_requests.get_request(worker, request_id)
    assert request["picked_at"] is not None and request["done_at"] is None
    assert runs(worker) == [(DEC1, "running", "catchUp", 1)]
    assert billing.get_run(worker, DEC1)["status"] == "running"
    # the next worker picks the request again: the date has its run, so the request is done
    assert process_requests(worker, clock=api.clock, tz=TZ) == [request_id]
    assert worker_requests.get_request(worker, request_id)["done_at"] is not None
    # and its wake-up finishes the unfinished run
    tick(worker, clock=api.clock, tz=TZ, run_at=time(0, 30))
    assert runs(worker) == [(DEC1, "completed", "catchUp", 2)]  # taken over: attempt 2
