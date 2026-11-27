"""The on-demand bill pass's stop, liveness and race paths (app/worker/on_demand.py, #511),
against the real test database: a checkpoint, the heartbeat of a demand that waits and of one
that is done, and a demand finished after the pass listed it."""
# ruff: noqa: F811

from datetime import date
from uuid import UUID

import pytest

from app.worker import on_demand
from app.worker.on_demand import process_demands
from tests import apidata
from tests.builders.billing.billing_completion import demand, run, worker  # noqa: F401
from tests.builders.billing.billing_db import add, at
from tests.timeline import TZ

pytestmark = pytest.mark.capability("worker.onDemand")


def ordered(api):
    """An account with a product from Dec 2 and an on-demand bill request."""
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 2), on=at(date(2026, 12, 2)))
    return account, demand(api, account)


def state(api, request):
    return api.get(request["href"]).json()["state"]


def test_a_checkpoint_stops_before_the_next_demand(api, worker):
    _, request = ordered(api)
    run(worker, api, date(2026, 12, 3))
    assert process_demands(worker, clock=api.clock, tz=TZ, checkpoint=lambda: True) == []
    assert state(api, request) == "inProgress"
    assert process_demands(worker, clock=api.clock, tz=TZ, checkpoint=lambda: False) == [
        UUID(request["id"])
    ]
    assert state(api, request) == "done"


def test_a_waiting_demand_and_a_done_one_each_get_a_heartbeat(api, worker):
    _, request = ordered(api)
    beats = []
    # no run since the request: it waits, and the worker is still alive
    assert process_demands(worker, clock=api.clock, tz=TZ, heartbeat=lambda: beats.append(1)) == []
    assert beats == [1] and state(api, request) == "inProgress"
    run(worker, api, date(2026, 12, 3))
    assert process_demands(worker, clock=api.clock, tz=TZ, heartbeat=lambda: beats.append(1)) == [
        UUID(request["id"])
    ]
    assert beats == [1, 1] and state(api, request) == "done"


def test_a_demand_done_after_the_pass_listed_it_is_left_alone(api, worker, monkeypatch):
    _, request = ordered(api)
    run(worker, api, date(2026, 12, 3))
    listed = on_demand.fetch_all(
        worker, "SELECT id, billing_account_id FROM customer_bill_on_demand"
    )
    worker.commit()
    assert process_demands(worker, clock=api.clock, tz=TZ) == [UUID(request["id"])]
    bill = api.get(request["href"]).json()["customerBill"]
    real = on_demand.fetch_all
    calls = []

    def stale_listing(conn, query, *args):
        calls.append(query)
        return listed if len(calls) == 1 else real(conn, query, *args)

    with monkeypatch.context() as patch:
        patch.setattr(on_demand, "fetch_all", stale_listing)
        assert process_demands(worker, clock=api.clock, tz=TZ) == []
    result = api.get(request["href"]).json()
    assert result["state"] == "done" and result["customerBill"] == bill
    bills = api.get(f"{apidata.BILL}?billingAccount.id={result['billingAccount']['id']}").json()
    assert len(bills) == 1
