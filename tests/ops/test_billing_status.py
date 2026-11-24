"""GET /billing-status (app/status.py): the freshness signal for the external monitor -
date, status and age only, 200 when billing is up to date, 503 otherwise (D-W7, D-W19,
Q6). The path belongs to no spec, so the responses are checked against the strict schema
below through the plain test client."""

from datetime import date, datetime, time

import pytest
from jsonschema import Draft7Validator

from app.repository import billing
from app.status import expected_date, freshness
from app.worker import db as worker_db
from app.worker.process import bill_date
from app.worker.runs import start_run
from tests.builders.worker.worker_failures import accounts_with_products
from tests.builders.worker.worker_process import DEC1, DEC2, scenario
from tests.timeline import TZ

RUN_AT = time(0, 30)
BODY = {
    "type": "object",
    "additionalProperties": False,  # nothing beyond these three: no amounts, names, counts
    "required": ["date", "status", "ageSeconds"],
    "properties": {
        "date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "status": {"enum": ["ok", "stale", "failed", "running", "none"]},
        "ageSeconds": {"type": ["integer", "null"], "minimum": 0},
    },
}


def msk(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=TZ)


def status_of(api) -> tuple[int, dict]:
    response = api.client.get("/billing-status")
    body = response.json()
    assert not list(Draft7Validator(BODY).iter_errors(body)), body
    return response.status_code, body


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


# --- the pure rules ---


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (msk(DEC2, 2, 29, 59), DEC1),  # the run of Dec 2 has until 02:30 (00:30 + 2 h)
        (msk(DEC2, 2, 30), DEC2),
        (msk(DEC2, 0, 0), DEC1),
        (msk(DEC2, 23, 59), DEC2),
    ],
)
def test_the_expected_date_includes_a_two_hour_grace(now, expected):
    assert expected_date(now, RUN_AT) == expected


def run(status: str, run_date: date, started: datetime, finished: datetime | None = None):
    return {"run_date": run_date, "status": status, "started_at": started, "finished_at": finished}


@pytest.mark.parametrize(
    ("latest", "now", "code", "status"),
    [
        (run("completed", DEC1, msk(DEC1, 0, 30), msk(DEC1, 0, 31)), msk(DEC2, 2, 29), 200, "ok"),
        (
            run("completed", DEC1, msk(DEC1, 0, 30), msk(DEC1, 0, 31)),
            msk(DEC2, 2, 30),
            503,
            "stale",
        ),
        (run("failed", DEC2, msk(DEC2, 0, 30), msk(DEC2, 0, 31)), msk(DEC2, 0, 32), 503, "failed"),
        (run("running", DEC2, msk(DEC2, 0, 30)), msk(DEC2, 2, 29), 200, "running"),
        (run("running", DEC2, msk(DEC2, 0, 30)), msk(DEC2, 2, 30), 503, "running"),
    ],
    ids=["fresh", "stale", "failed", "running-in-grace", "running-overdue"],
)
def test_the_latest_run_decides(latest, now, code, status):
    got_code, body = freshness(latest, now, TZ, RUN_AT)
    assert (got_code, body["status"], body["date"]) == (
        code,
        status,
        latest["run_date"].isoformat(),
    )


def test_the_age_counts_from_the_end_or_the_start():
    done = run("completed", DEC1, msk(DEC1, 0, 30), msk(DEC1, 0, 40))
    assert freshness(done, msk(DEC1, 1, 40), TZ, RUN_AT)[1]["ageSeconds"] == 3600
    going = run("running", DEC2, msk(DEC2, 0, 30))
    assert freshness(going, msk(DEC2, 0, 40), TZ, RUN_AT)[1]["ageSeconds"] == 600


# --- through the app ---


def test_no_run_at_all_is_503_none(api):
    assert status_of(api) == (503, {"date": None, "status": "none", "ageSeconds": None})


def test_a_completed_run_is_fresh_until_the_next_one_is_overdue(api, worker):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    api.clock.set(msk(DEC2, 2, 29, 59))
    code, body = status_of(api)
    assert (code, body["date"], body["status"]) == (200, "2026-12-01", "ok")
    api.clock.set(msk(DEC2, 2, 30))  # the run of Dec 2 is now overdue
    code, body = status_of(api)
    assert (code, body["date"], body["status"]) == (503, "2026-12-01", "stale")
    # a late run must not mark the app unhealthy: /healthz stays 200
    assert api.client.get("/healthz").status_code == 200


def test_a_failed_run_is_503_failed_without_names_or_amounts(api, worker):
    scenario(api, worker, broken=True)  # Dec 1-3 failed because of account x
    code, body = status_of(api)
    assert (code, body["date"], body["status"]) == (503, "2026-12-03", "failed")
    assert set(body) == {"date", "status", "ageSeconds"}


def test_a_running_run_is_503_once_overdue(api, worker):
    accounts_with_products(api)
    api.clock.set(msk(DEC1, 0, 30))
    start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    api.clock.set(msk(DEC1, 2, 29))
    assert status_of(api)[0] == 200
    api.clock.set(msk(DEC1, 2, 30))
    code, body = status_of(api)
    assert (code, body["status"], body["ageSeconds"]) == (503, "running", 7200)
    assert billing.get_run(worker, DEC1)["status"] == "running"


def test_the_grace_of_a_taken_over_run_counts_from_the_takeover(api, worker, migrated_db_url):
    """doc-findings #7: a run whose worker died (a deploy, the 03:00 reboot) and was taken
    over gets the full 2 h from the takeover, not from the dead worker's start."""
    accounts_with_products(api)
    first = worker_db.connect(migrated_db_url)
    start_run(first, DEC1, now=msk(DEC1, 0, 30), trigger="scheduled")
    first.close()  # the owner dies; its lock goes with the connection
    taken = start_run(worker, DEC1, now=msk(DEC1, 2, 0), trigger="scheduled")
    assert taken.how == "takenOver"
    api.clock.set(msk(DEC1, 3, 59))  # 3 h 29 min after the first start
    code, body = status_of(api)
    assert (code, body["status"], body["ageSeconds"]) == (200, "running", 7140)
    api.clock.set(msk(DEC1, 4, 0))
    assert status_of(api)[:1] == (503,)
    assert status_of(api)[1]["status"] == "running"


def test_a_run_taken_over_again_and_again_is_overdue_once_its_date_is_late():
    """The grace restarts on every takeover, so a worker that keeps dying would never let
    it run out: a running run of a date before the expected one is 503 regardless."""
    looping = run("running", DEC1, msk(DEC2, 2, 0))  # just taken over again
    assert freshness(looping, msk(DEC2, 2, 29), TZ, RUN_AT)[0] == 200  # expected: Dec 1
    code, body = freshness(looping, msk(DEC2, 2, 30), TZ, RUN_AT)  # expected: Dec 2
    assert (code, body["status"], body["date"]) == (503, "running", "2026-12-01")


def test_unknown_query_parameters_are_400(api):
    assert api.client.get("/billing-status?verbose=1").status_code == 400
