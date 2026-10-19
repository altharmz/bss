"""Suspension and resumption: TMF622 modify items with `product.status`, effective
requestedStartDate (docs/decisions/tmf-api.md#suspension-2026-10-19). Billing does not
change: no spec says what a suspension does to charges.

Offering A costs 300 a month; the account's cycle day is 1."""

from datetime import date

import pytest

from tests import apidata
from tests.test_api_order_cancel import CANCEL, cancel_body, rates, refused, run, worker
from tests.test_billing_db import add, at, delete
from tests.test_events import subscribe, types
from tests.timeline import iso, midnight, z

__all__ = ["worker"]  # the fixture, imported for these tests

OCT2, OCT20, NOV1, NOV5, NOV11 = (
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 1),
    date(2026, 11, 5),
    date(2026, 11, 11),
)


@pytest.fixture
def shop(api):
    return apidata.sellable_offering(api, value=300, name="Fiber 100"), apidata.account(api)


def status_item(product: dict, status: str, item_id: str = "1", **over) -> dict:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "modify",
        "product": {"id": product["id"], "status": status, "@type": "ProductRef"},
        **over,
    }


def change(api, acc, product, status: str, day: date | None, *, on) -> dict:
    api.clock.set(on)
    over = {} if day is None else {"requestedStartDate": iso(midnight(day))}
    return apidata.order(api, acc, status_item(product, status), **over)


def errors_of(order: dict) -> list[str]:
    return [e["code"] for e in order.get("productOrderErrorMessage", [])]


def status_of(api, product: dict) -> str:
    return api.get(product["href"]).json()["status"]


def started(api, shop) -> dict:
    offering, acc = shop
    return add(api, acc, offering, OCT2, on=at(date(2026, 10, 1)))


# --- shape ---


def test_an_item_changes_the_offering_or_the_status_never_both(api, shop):
    offering, acc = shop
    product = started(api, shop)
    both = status_item(
        product, "suspended", productOffering={"id": offering["id"], "@type": "ProductOfferingRef"}
    )
    on_delete = apidata.delete_item(product["id"])
    on_delete["product"]["status"] = "suspended"
    for item in (both, on_delete, status_item(product, "terminated")):
        response = api.post(apidata.ORDER, apidata.order_body(acc, item))
        assert response.status_code == 422, response.text


# --- execution ---


def test_suspension_today_and_resumption(api, shop, db_conn):
    offering, acc = shop
    product = started(api, shop)
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    placed = change(api, acc, product, "suspended", None, on=at(OCT20))
    assert placed["state"] == "completed"
    (item,) = placed["productOrderItem"]
    assert item["product"]["status"] == "suspended" and "productOffering" not in item
    suspended = api.get(product["href"]).json()
    assert suspended["status"] == "suspended"
    assert [i["role"] for i in suspended["productOrderItem"]] == ["creation", "modification"]
    assert types(db_conn, "TMF637") == ["ProductStateChangeEvent"]
    # one status change per day, as one offering change per day
    assert errors_of(change(api, acc, product, "active", None, on=at(OCT20))) == ["openModifyItem"]
    assert change(api, acc, product, "active", None, on=at(NOV1))["state"] == "completed"
    assert status_of(api, product) == "active"


def test_a_future_suspension_takes_effect_with_the_run_of_its_day(api, shop, worker, db_conn):
    offering, acc = shop
    product = started(api, shop)
    run(worker, api, OCT2)  # activates it
    change(api, acc, product, "suspended", NOV5, on=at(OCT20))
    assert status_of(api, product) == "active"  # no pending state in the spec
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    run(worker, api, NOV1)
    assert status_of(api, product) == "active" and types(db_conn, "TMF637") == []
    run(worker, api, NOV5)
    assert status_of(api, product) == "suspended"
    assert types(db_conn, "TMF637") == ["ProductStateChangeEvent"]
    run(worker, api, NOV11)  # once
    assert types(db_conn, "TMF637") == ["ProductStateChangeEvent"]


def test_a_product_suspended_from_its_start_is_activated_first(api, shop, worker):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    assert change(api, acc, product, "suspended", NOV1, on=at(OCT20))["state"] == "completed"
    run(worker, api, NOV1)
    assert status_of(api, product) == "suspended"


def test_billing_does_not_change(api, shop, worker):
    offering, acc = shop
    product = started(api, shop)
    change(api, acc, product, "suspended", None, on=at(OCT20))
    run(worker, api, NOV1)
    assert (z(midnight(NOV1)), 300) in rates(api, product)


# --- rules ---


def test_rejections(api, shop):
    offering, acc = shop
    product = started(api, shop)
    other = apidata.account(api, name="Other")
    theirs = add(api, other, offering, OCT2, on=at(date(2026, 10, 1)))
    later = add(api, acc, offering, NOV11, on=at(OCT20))
    on = at(OCT20)
    assert errors_of(change(api, acc, theirs, "suspended", NOV1, on=on)) == [
        "productOfOtherAccount"
    ]
    assert errors_of(change(api, acc, product, "active", NOV1, on=on)) == ["statusUnchanged"]
    assert errors_of(change(api, acc, later, "suspended", NOV5, on=on)) == ["productNotStarted"]
    assert change(api, acc, product, "suspended", NOV5, on=on)["state"] == "completed"
    # one change waits at a time, and changes come in date order
    assert errors_of(change(api, acc, product, "active", NOV11, on=on)) == ["openModifyItem"]
    ending = add(api, acc, offering, OCT20, on=on)
    delete(api, acc, ending, NOV11, on=on)
    assert errors_of(change(api, acc, ending, "suspended", NOV1, on=on)) == ["productAlreadyEnded"]


def test_a_delete_waits_for_a_pending_status_change(api, shop):
    offering, acc = shop
    product = started(api, shop)
    change(api, acc, product, "suspended", NOV5, on=at(OCT20))
    placed = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(NOV11))
    )
    assert errors_of(placed) == ["openModifyItem"]


def test_a_suspended_product_can_be_ended_and_the_end_undone(api, shop):
    offering, acc = shop
    product = started(api, shop)
    change(api, acc, product, "suspended", None, on=at(OCT20))
    api.clock.set(at(OCT20))
    ending = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(NOV11))
    )
    assert ending["state"] == "completed" and status_of(api, product) == "pendingTerminate"
    assert api.post(CANCEL, cancel_body(ending)).status_code == 201
    assert status_of(api, product) == "suspended"


def test_cancelling_a_future_status_change(api, shop, worker):
    offering, acc = shop
    product = started(api, shop)
    placed = change(api, acc, product, "suspended", NOV5, on=at(OCT20))
    assert api.post(CANCEL, cancel_body(placed)).status_code == 201
    run(worker, api, NOV5)
    assert status_of(api, product) == "active"
    today = change(api, acc, product, "suspended", None, on=at(NOV5, 12))
    refused(api, today, "took effect")


def test_an_inactive_account_takes_ends_suspensions_and_resumptions(api, shop):
    offering, acc = shop
    product = started(api, shop)
    second = add(api, acc, offering, OCT2, on=at(date(2026, 10, 1)))
    api.clock.set(at(OCT20))
    assert api.patch(acc["href"], {"state": "Suspended"}).status_code == 200
    assert change(api, acc, product, "suspended", None, on=at(OCT20))["state"] == "completed"
    assert change(api, acc, product, "active", None, on=at(NOV1))["state"] == "completed"
    delete(api, acc, second, None, on=at(NOV1))
    added = apidata.order(api, acc, apidata.add_item(offering))
    assert errors_of(added) == ["accountNotActive"]


# --- TMF637 PATCH status ---


def test_patch_status_places_the_order(api, shop):
    offering, acc = shop
    product = started(api, shop)
    api.clock.set(at(OCT20))
    response = api.patch(product["href"], {"status": "suspended"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"
    (_, modification) = response.json()["productOrderItem"]
    assert modification["role"] == "modification"
    api.clock.set(at(NOV1))
    again = api.patch(product["href"], {"status": "suspended"})
    assert again.status_code == 409 and "statusUnchanged" in again.json()["message"]
    assert api.patch(product["href"], {"status": "active"}).json()["status"] == "active"
