"""`pendingChanges` on the internal open products (docs/ui.md)."""

from datetime import date, timedelta
from uuid import UUID

from tests import apidata
from tests.builders.billing.billing_db import add, at
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.orders.api_order_modify import modify
from tests.builders.orders.api_suspension import OCT2, change
from tests.builders.prepaid.prepaid_offering_changes import granted, switch  # noqa: F401 - fixtures
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick

BASE = "/internal/v1/billingAccount"
NOV3, NOV11 = date(2026, 11, 3), date(2026, 11, 11)


def listed(api, acc) -> dict:
    response = api.get(f"{BASE}/{acc['id']}/products")
    assert response.status_code == 200, response.text
    return {p["id"]: p for p in response.json()}


def test_a_future_suspension_and_offering_change_are_pending_then_applied(api):
    a = apidata.sellable_offering(api, value=300, name="Fiber 100")
    b = apidata.sellable_offering(api, value=600, name="Fiber Plus")
    acc = apidata.account(api)
    product = add(api, acc, a, OCT2, on=at(date(2026, 10, 1)))
    assert listed(api, acc)[product["id"]]["pendingChanges"] == []

    suspend = change(api, acc, product, "suspended", NOV3, on=at(date(2026, 10, 5)))
    switched = modify(api, acc, product, b, NOV11, on=at(date(2026, 10, 5), 10))
    pending = listed(api, acc)[product["id"]]["pendingChanges"]
    assert [(c["kind"], c["date"]) for c in pending] == [
        ("status", "2026-11-03"),
        ("offering", "2026-11-11"),
    ]
    assert pending[0]["status"] == "suspended" and "productOffering" not in pending[0]
    assert pending[0]["productOrder"] == {
        "id": suspend["id"],
        "href": f"/tmf-api/productOrderingManagement/v5/productOrder/{suspend['id']}",
    }
    assert pending[1]["productOffering"] == {"id": b["id"], "name": "Fiber Plus"}
    assert pending[1]["productOrder"]["id"] == switched["id"]

    api.clock.set(at(NOV3 + timedelta(days=1)))  # the status change day has come
    assert [c["kind"] for c in listed(api, acc)[product["id"]]["pendingChanges"]] == ["offering"]
    api.clock.set(at(NOV11 + timedelta(days=1)))  # the offering change is in effect
    assert listed(api, acc)[product["id"]]["pendingChanges"] == []


def test_an_undone_change_disappears(api):
    a = apidata.sellable_offering(api, value=300)
    acc = apidata.account(api)
    product = add(api, acc, a, OCT2, on=at(date(2026, 10, 1)))
    order = change(api, acc, product, "suspended", NOV3, on=at(date(2026, 10, 5)))
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert listed(api, acc)[product["id"]]["pendingChanges"] == []


def test_a_prepaid_change_at_paid_through_is_pending_until_the_funded_renewal(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    switch(api, granted.acc, first, granted.b, first["due"])
    api.clock.set(at(first["due"], 12))
    shown = listed(api, granted.acc)[first["id"]]["pendingChanges"]
    assert [(c["kind"], c["date"]) for c in shown] == [("offering", first["due"].isoformat())]
    assert shown[0]["productOffering"]["id"] == granted.b["id"]  # waits for funds when due

    pay(db_conn, UUID(granted.acc["id"]), "18", api.clock.now())
    tick(db_conn, at(first["due"], 0))
    assert listed(api, granted.acc)[first["id"]]["pendingChanges"] == []


def test_same_day_status_undo_eligibility(api):
    a = apidata.sellable_offering(api)
    acc = apidata.account(api)
    product = add(api, acc, a, OCT2, on=at(date(2026, 10, 1)))
    day = date(2026, 10, 5)
    order = change(api, acc, product, "suspended", day, on=at(day))
    assert listed(api, acc)[product["id"]]["undoableStatusChange"] == {
        "date": day.isoformat(),
        "status": "suspended",
        "productOrder": {"id": order["id"]},
    }
    api.clock.set(at(day + timedelta(days=1)))
    assert listed(api, acc)[product["id"]]["undoableStatusChange"] is None
    api.clock.set(at(day))
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert listed(api, acc)[product["id"]]["undoableStatusChange"] is None


def test_future_suspension_pending_undo_restores_active(api):
    a = apidata.sellable_offering(api)
    acc = apidata.account(api)
    product = add(api, acc, a, OCT2, on=at(OCT2))
    order = change(api, acc, product, "suspended", NOV3, on=at(date(2026, 10, 5)))
    row = listed(api, acc)[product["id"]]
    assert row["undoableStatusChange"] is None
    assert row["pendingChanges"][0]["productOrder"]["id"] == order["id"]
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    row = listed(api, acc)[product["id"]]
    assert row["pendingChanges"] == [] and row["status"] == "active"


def test_same_day_status_undo_hidden_when_termination_blocks_cancellation(api):
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    day = date(2026, 10, 5)
    change(api, acc, product, "suspended", day, on=at(day))
    assert listed(api, acc)[product["id"]]["undoableStatusChange"] is not None
    ended = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate="2026-11-03T00:00:00Z"
    )
    assert ended["state"] == "completed"
    assert listed(api, acc)[product["id"]]["undoableStatusChange"] is None
