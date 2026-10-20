"""Billing runs in the internal API (app/internal/billing_runs.py) and the worker's request
queue (app/worker/queue.py; docs/stage4-design.md section 7).

The API only reads runs and queues requests - it never bills; the worker carries the
requests out. Every response here is checked against spec/internal by the test client.
"""

from datetime import datetime, time
from uuid import UUID

import pytest

from app.repository import billing, worker_requests
from app.worker import db as worker_db
from app.worker import queue
from app.worker.plan import catch_up_dates
from app.worker.process import RunAborted, bill_date
from app.worker.queue import process_requests
from app.worker.runs import RunInProgress
from tests.test_worker_failures import accounts_with_products, fail_for
from tests.test_worker_process import DEC1, DEC2, DEC3, at, scenario, set_currency
from tests.timeline import TZ

RUNS = "/internal/v1/billingRun"


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def runs_in_creation_order(conn) -> list[tuple]:
    return conn.execute(
        "SELECT run_date, status::text, trigger::text FROM billing_run ORDER BY created_at, id"
    ).fetchall()


# --- the catch-up dates (pure) ---


@pytest.mark.parametrize(
    ("latest", "held", "up_to", "dates"),
    [
        (None, None, DEC3, [DEC3]),  # first run ever: just the date (D-W15)
        (DEC1, None, DEC3, [DEC2, DEC3]),
        (DEC3, None, DEC3, []),  # nothing due
        (DEC1, DEC1, DEC3, [DEC1, DEC2, DEC3]),  # the run that failed as a whole first
        (DEC2, DEC2, DEC2, [DEC2]),
    ],
)
def test_catch_up_dates(latest, held, up_to, dates):
    assert catch_up_dates(latest, held, up_to) == dates


# --- reading runs ---


def test_runs_are_listed_newest_first_with_derived_counts(api, worker):
    s = scenario(api, worker, broken=True)  # x failed Dec 1, blocked Dec 2 and 3
    response = api.get(RUNS)
    assert response.headers["X-Total-Count"] == "3"
    runs = response.json()
    assert [r["runDate"] for r in runs] == ["2026-12-03", "2026-12-02", "2026-12-01"]
    assert [r["isLatest"] for r in runs] == [True, False, False]
    dec1, dec2 = runs[2], runs[1]
    assert (dec1["status"], dec1["failureKind"], dec1["trigger"]) == (
        "failed",
        "accounts",
        "scheduled",
    )
    assert dec1["accounts"] == {"total": 3, "ok": 2, "failed": 1, "blocked": 0}
    assert dec2["accounts"] == {"total": 3, "ok": 2, "failed": 0, "blocked": 1}
    assert dec1["error"] == "1 of 3 accounts not billed (1 failed)"
    # Dec 1 charged a and b (300 each); Dec 2 charged nothing
    assert dec1["charges"] == {"count": 2, "amounts": [{"unit": "USD", "value": 600}]}
    assert dec2["charges"] == {"count": 0, "amounts": []}
    one = api.get(f"{RUNS}/{s['runs'][DEC1]['id']}").json()
    assert one == dec1
    page = api.get(f"{RUNS}?offset=1&limit=1")
    assert [r["runDate"] for r in page.json()] == ["2026-12-02"]
    assert page.headers["X-Result-Count"] == "1"


def test_a_runs_accounts_with_their_outcomes(api, worker):
    s = scenario(api, worker, broken=True)
    dec2 = s["runs"][DEC2]["id"]
    every = api.get(f"{RUNS}/{dec2}/accounts").json()
    assert sorted(a["status"] for a in every) == ["blocked", "ok", "ok"]
    (blocked,) = api.get(f"{RUNS}/{dec2}/accounts?status=blocked").json()
    assert blocked["billingAccount"]["id"] == str(s["ids"]["x"])
    assert blocked["waitsFor"] == {"id": str(s["runs"][DEC1]["id"]), "runDate": "2026-12-01"}
    (failed,) = api.get(f"{RUNS}/{s['runs'][DEC1]['id']}/accounts?status=failed").json()
    assert failed["error"].startswith("MissingPrice: ") and failed["waitsFor"] is None


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("?status=nope", 400),
        ("?status=ok&status=failed", 400),
        ("?foo=1", 400),
        ("?limit=101", 400),
    ],
)
def test_run_accounts_query_rules(api, worker, query, status):
    s = scenario(api, worker, broken=False)
    assert api.get(f"{RUNS}/{s['runs'][DEC1]['id']}/accounts{query}").status_code == status


def test_unknown_runs_are_404(api):
    for path in (f"{RUNS}/00000000-0000-0000-0000-000000000000", f"{RUNS}/not-a-uuid"):
        assert api.get(path).status_code == 404
        assert api.get(f"{path}/accounts").status_code == 404
        assert api.post(f"{path}/retry", None).status_code == 404
    assert api.get(f"{RUNS}?foo=1").status_code == 400


# --- queueing: catch-up ---


def test_catch_up_is_refused_before_the_run_is_due(api):
    """Q5: the run of D is due at BILLING_RUN_AT (00:30), not at 00:00 - the margin covers
    orders completed before midnight whose transaction commits after it."""
    api.clock.set(datetime.combine(DEC3, time(0, 29, 59), tzinfo=TZ))
    late = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"})
    assert late.status_code == 422
    assert "due at 00:30" in late.json()["message"]
    assert api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-04"}).status_code == 422  # future
    api.clock.set(datetime.combine(DEC3, time(0, 30), tzinfo=TZ))
    assert api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"}).status_code == 202


def test_catch_up_queues_a_request_and_says_which_dates(api, db_conn):
    api.clock.set(at(DEC3, 12))
    # requestedBy is the signed-in user; a client-sent X-Remote-User is ignored
    response = api.post(
        f"{RUNS}/catchUp", {"upTo": "2026-12-02"}, headers={"X-Remote-User": "forged"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["dates"] == ["2026-12-02"]  # no run yet: just the date
    request = body["request"]
    assert (request["kind"], request["upTo"], request["run"]) == ("catchUp", "2026-12-02", None)
    assert (request["requestedBy"], request["pickedAt"], request["doneAt"]) == (
        "i.petrova",
        None,
        None,
    )
    # the API never bills: a queued request, no run, no charge
    assert (count(db_conn, "worker_request"), count(db_conn, "billing_run")) == (1, 0)
    assert count(db_conn, "applied_customer_billing_rate") == 0


def test_a_request_from_mcp_is_requested_by_its_principal(api, worker):
    s = scenario(api, worker, broken=True)
    mcp = {"X-Bss-Service": "test-service-credential", "X-Remote-User": "mcp:demo"}
    response = api.post(f"{RUNS}/{s['runs'][DEC1]['id']}/retry", None, headers=mcp)
    assert response.status_code == 202
    assert response.json()["request"]["requestedBy"] == "mcp:demo"


def test_catch_up_when_nothing_is_due_queues_nothing(api, worker):
    scenario(api, worker, broken=False)  # runs up to Dec 3
    api.clock.set(at(DEC3, 12))
    response = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"})
    assert (response.status_code, response.json()) == (200, {"dates": []})
    assert count(worker, "worker_request") == 0


@pytest.mark.parametrize(
    ("body", "status"),
    [({"upTo": "2026-12-01T00:00"}, 422), ({}, 422), ({"upTo": "2026-12-01", "x": 1}, 400)],
)
def test_catch_up_body_rules(api, body, status):
    api.clock.set(at(DEC3, 12))
    assert api.post(f"{RUNS}/catchUp", body).status_code == status


# --- queueing: retry ---


def test_retry_queues_a_request_for_a_failed_run_once(api, worker):
    s = scenario(api, worker, broken=True)
    dec1 = s["runs"][DEC1]
    response = api.post(f"{RUNS}/{dec1['id']}/retry", None)
    assert response.status_code == 202
    request = response.json()["request"]
    assert (request["kind"], request["upTo"]) == ("retry", None)
    assert request["run"] == {"id": str(dec1["id"]), "runDate": "2026-12-01"}
    again = api.post(f"{RUNS}/{dec1['id']}/retry", None)
    assert again.status_code == 409 and "already queued" in again.json()["message"]


def test_only_a_failed_run_can_be_retried(api, worker):
    s = scenario(api, worker, broken=False)
    response = api.post(f"{RUNS}/{s['runs'][DEC1]['id']}/retry", None)
    assert response.status_code == 409
    assert "only a failed run" in response.json()["message"]


# --- the worker carries the requests out ---


@pytest.mark.capability("worker.requestQueue")
def test_a_catch_up_request_runs_the_dates_in_order(api, worker):
    accounts_with_products(api)
    api.clock.set(at(DEC1, 0, 30))
    bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    api.clock.set(at(DEC3, 12))
    queued = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-03"}).json()
    assert queued["dates"] == ["2026-12-02", "2026-12-03"]
    assert process_requests(worker, clock=api.clock, tz=TZ) == [UUID(queued["request"]["id"])]
    assert runs_in_creation_order(worker) == [
        (DEC1, "completed", "scheduled"),
        (DEC2, "completed", "catchUp"),
        (DEC3, "completed", "catchUp"),
    ]
    done = worker_requests.get_request(worker, UUID(queued["request"]["id"]))
    assert done["picked_at"] is not None and done["done_at"] is not None
    assert process_requests(worker, clock=api.clock, tz=TZ) == []  # nothing left


@pytest.mark.capability("worker.requestQueue")
def test_a_catch_up_puts_a_run_failed_as_a_whole_first(api, worker, monkeypatch):
    _, second, _ = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    restore()
    api.clock.set(at(DEC1, 0, 31))  # inside the backoff: an operator is not held to it
    queued = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-01"}).json()
    assert queued["dates"] == ["2026-12-01"]
    process_requests(worker, clock=api.clock, tz=TZ)
    run = billing.get_run(worker, DEC1)
    assert (run["status"], run["attempt"], run["trigger"]) == ("completed", 2, "catchUp")


@pytest.mark.capability("worker.requestQueue")
def test_a_retry_request_retries_only_the_failed_accounts(api, worker):
    s = scenario(api, worker, broken=True)
    set_currency(worker, s["fragile"]["id"], "USD")  # the fix
    api.clock.set(at(DEC3, 12))
    api.post(f"{RUNS}/{s['runs'][DEC1]['id']}/retry", None)
    ok_rows = {
        i: billing.outcomes(worker, s["runs"][DEC1]["id"])[i]
        for i in (s["ids"]["a"], s["ids"]["b"])
    }
    process_requests(worker, clock=api.clock, tz=TZ)
    assert [billing.get_run(worker, d)["status"] for d in (DEC1, DEC2, DEC3)] == ["completed"] * 3
    after = billing.outcomes(worker, s["runs"][DEC1]["id"])
    assert {i: after[i] for i in ok_rows} == ok_rows  # a and b untouched
    assert after[s["ids"]["x"]]["status"] == "ok"


@pytest.mark.capability("worker.requestQueue")
def test_a_retry_request_reruns_a_run_failed_as_a_whole(api, worker, monkeypatch):
    _, second, _ = accounts_with_products(api)
    restore = fail_for(monkeypatch, second)
    api.clock.set(at(DEC1, 0, 30))
    with pytest.raises(RunAborted):
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
    restore()
    run_id = billing.get_run(worker, DEC1)["id"]
    api.post(f"{RUNS}/{run_id}/retry", None)
    process_requests(worker, clock=api.clock, tz=TZ)  # well inside the 5-minute backoff
    run = billing.get_run(worker, DEC1)
    assert (run["status"], run["attempt"], run["trigger"]) == ("completed", 2, "retry")


@pytest.mark.capability("worker.requestQueue")
def test_a_request_that_raises_is_done_and_does_not_block_the_queue(api, worker, monkeypatch):
    accounts_with_products(api)
    api.clock.set(at(DEC3, 12))
    first = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-01"}).json()["request"]
    second = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-02"}).json()["request"]
    real = queue.carry_out

    def explodes_once(conn, request, **kw):
        if str(request["id"]) == first["id"]:
            raise ValueError("a bug in one request")
        return real(conn, request, **kw)

    monkeypatch.setattr(queue, "carry_out", explodes_once)
    done = process_requests(worker, clock=api.clock, tz=TZ)
    assert done == [UUID(first["id"]), UUID(second["id"])]
    assert all(worker_requests.get_request(worker, i)["done_at"] for i in done)
    # the second still ran: Dec 2 (no run before it, so just its date)
    assert runs_in_creation_order(worker) == [(DEC2, "completed", "catchUp")]


@pytest.mark.capability("worker.requestQueue")
def test_a_request_picked_by_a_worker_that_died_is_picked_again(api, worker):
    accounts_with_products(api)
    api.clock.set(at(DEC3, 12))
    request = api.post(f"{RUNS}/catchUp", {"upTo": "2026-12-01"}).json()["request"]
    worker_requests.mark_picked(worker, UUID(request["id"]), api.clock.now())  # then died
    assert process_requests(worker, clock=api.clock, tz=TZ) == [UUID(request["id"])]
    assert billing.get_run(worker, DEC1)["status"] == "completed"


def test_only_the_owner_processes_requests(api, worker, migrated_db_url):
    owner = worker_db.connect(migrated_db_url)
    try:
        assert worker_db.acquire_ownership(owner)
        with pytest.raises(RunInProgress):
            process_requests(worker, clock=api.clock, tz=TZ)
    finally:
        owner.close()


def test_a_retry_of_a_run_fixed_in_the_meantime_does_nothing(api, worker):
    s = scenario(api, worker, broken=True)
    set_currency(worker, s["fragile"]["id"], "USD")
    api.post(f"{RUNS}/{s['runs'][DEC1]['id']}/retry", None)
    api.post(f"{RUNS}/{s['runs'][DEC2]['id']}/retry", None)
    process_requests(worker, clock=api.clock, tz=TZ)  # Dec 1 retry fixes Dec 2 as well
    assert billing.get_run(worker, DEC2)["status"] == "completed"
    assert count(worker, "worker_request") == 2
