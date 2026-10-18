"""TMF622 cancelProductOrder and DELETE productOrder.

Orders complete synchronously, so a cancellation undoes a completed order before any of
it takes effect: new products that have not started are cancelled, terminations that
have not happened are taken back - unless billing already charged the period involved.
Anything else is a 409 and nothing is stored. Only a rejected order can be deleted.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import bill_account
from app.repository import billing
from app.worker import db as worker_db
from app.worker.process import bill_date
from app.worker.runs import start_run
from tests import apidata, timeline
from tests.test_api_catalog_delete import _finish, _waits
from tests.test_billing_db import add, at, delete
from tests.timeline import TZ, iso, midnight

BASE = "/tmf-api/productOrderingManagement/v5"
CANCEL = f"{BASE}/cancelProductOrder"
OCT2, OCT20, NOV1, NOV5 = (
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 1),
    date(2026, 11, 5),
)
DEC1 = date(2026, 12, 1)


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def shop(api):
    return apidata.sellable_offering(api, value=300), apidata.account(api)


def run(worker, api, day: date) -> None:
    api.clock.set(at(day, 0))
    bill_date(worker, day, clock=api.clock, tz=TZ, trigger="scheduled")


def cancel_body(order: dict, **over) -> dict:
    return {"@type": "CancelProductOrder", "productOrder": {"id": order["id"]}, **over}


def order_of(api, product: dict, role: str) -> dict:
    (related,) = [r for r in product["productOrderItem"] if r["role"] == role]
    return api.get(related["orderHref"]).json()


def refused(api, order: dict, *expected: str, stored: int = 0) -> None:
    """A 409 naming `expected`; the order unchanged and no (further) cancellation stored."""
    before = api.get(order["href"]).json()
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 409, response.text
    for text in expected:
        assert text in response.json()["message"], response.json()["message"]
    assert api.get(order["href"]).json() == before
    assert len(api.get(f"{CANCEL}?productOrder.id={order['id']}").json()) == stored


def rates(api, product: dict) -> list[tuple[str, Decimal]]:
    listed = api.get(f"{apidata.RATE}?product.id={product['id']}").json()
    return [
        (r["periodCoverage"]["startDateTime"], Decimal(str(r["taxExcludedAmount"]["value"])))
        for r in listed
    ]


def test_cancelling_an_order_for_a_future_product(api, shop, worker):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    assert product["status"] == "created"
    order = order_of(api, product, "creation")
    response = api.post(CANCEL, cancel_body(order, cancellationReason="Changed mind"))
    assert response.status_code == 201, response.text
    cancel = response.json()
    assert cancel["state"] == "done"
    assert cancel["productOrder"]["id"] == order["id"]
    assert cancel["effectiveCancellationDate"] == cancel["creationDate"]
    assert response.headers["location"] == cancel["href"]
    assert api.get(cancel["href"]).json() == cancel
    assert [c["id"] for c in api.get(f"{CANCEL}?productOrder.id={order['id']}").json()] == [
        cancel["id"]
    ]
    cancelled = api.get(order["href"]).json()
    assert cancelled["state"] == "cancelled"
    assert cancelled["cancellationReason"] == "Changed mind"
    assert cancelled["cancellationDate"] == cancel["creationDate"]
    assert [i["state"] for i in cancelled["productOrderItem"]] == ["cancelled"]
    gone = api.get(product["href"]).json()
    assert gone["status"] == "cancelled" and gone["terminationDate"] == gone["startDate"]
    run(worker, api, NOV1)
    assert rates(api, product) == []  # never charged
    api.clock.set(at(NOV5))
    refused(api, order, "is cancelled", "only a completed order", stored=1)


def test_an_order_whose_product_started_stays(api, shop):
    offering, acc = shop
    product = add(api, acc, offering, OCT20, on=at(OCT20))
    refused(api, order_of(api, product, "creation"), "started on 2026-10-20")
    assert api.get(product["href"]).json()["status"] == "active"


def test_taking_back_a_termination_before_its_period_is_charged(api, shop, worker):
    offering, acc = shop
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    run(worker, api, NOV1)
    delete(api, acc, product, date(2026, 12, 15), on=at(NOV5))
    ending = api.get(product["href"]).json()
    assert ending["status"] == "pendingTerminate"
    order = order_of(api, ending, "termination")
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    restored = api.get(product["href"]).json()
    assert restored["status"] == "active" and "terminationDate" not in restored
    assert [r["role"] for r in restored["productOrderItem"]] == ["creation"]
    run(worker, api, DEC1)
    assert rates(api, product)[-1] == (iso_z(DEC1), Decimal("300.00"))  # December in full
    # the product can be ended again
    delete(api, acc, product, date(2027, 1, 1), on=at(date(2026, 12, 2)))


def iso_z(day: date) -> str:
    return timeline.z(midnight(day))


def test_a_termination_billing_already_reflects_stays(api, shop, worker):
    offering, acc = shop
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    run(worker, api, NOV1)  # November charged in full
    delete(api, acc, product, date(2026, 11, 15), on=at(NOV5))
    order = order_of(api, api.get(product["href"]).json(), "termination")
    refused(
        api,
        order,
        "item 1 (delete)",
        "billing already charged its product for the period from 2026-11-01",
    )
    assert api.get(product["href"]).json()["status"] == "pendingTerminate"


def test_a_termination_that_happened_stays(api, shop):
    offering, acc = shop
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    delete(api, acc, product, None, on=at(OCT20))  # today: terminated at once
    order = order_of(api, api.get(product["href"]).json(), "termination")
    refused(api, order, "the termination took effect on 2026-10-20")


def test_taking_back_the_cancellation_of_a_product_that_has_not_started(api, shop):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    delete(api, acc, product, None, on=at(OCT20))  # before its start: cancelled
    order = order_of(api, api.get(product["href"]).json(), "termination")
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    restored = api.get(product["href"]).json()
    assert restored["status"] == "created" and "terminationDate" not in restored


def test_an_add_whose_product_another_order_ended_stays(api, shop):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    delete(api, acc, product, None, on=at(OCT20))
    refused(api, order_of(api, api.get(product["href"]).json(), "creation"), "another order")


def test_cancellation_request_validation(api, shop):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    order = order_of(api, product, "creation")
    tomorrow = iso(midnight(date(2026, 10, 21)))
    response = api.post(CANCEL, cancel_body(order, requestedCancellationDate=tomorrow))
    assert response.status_code == 422
    assert "must be today (2026-10-20)" in response.json()["message"]
    assert api.post(CANCEL, cancel_body({"id": apidata.NIL})).status_code == 422
    # state is the server's (a stored cancellation is done): another value is a 422
    assert (
        api.post(CANCEL, {"productOrder": {"id": order["id"]}, "state": "failed"}).status_code
        == 422
    )
    assert api.post(CANCEL, {**cancel_body(order), "priority": "1"}).status_code == 400
    assert api.post(CANCEL, {"@type": "CancelProductOrder"}).status_code == 422
    rejected = apidata.order(api, acc, apidata.rejected_item(api, offering))
    refused(api, rejected, "is rejected")
    assert api.get(order["href"]).json()["state"] == "completed"
    today = iso(at(OCT20, 15))
    response = api.post(CANCEL, cancel_body(order, requestedCancellationDate=today))
    assert response.status_code == 201, response.text
    assert response.json()["requestedCancellationDate"] == timeline.z(at(OCT20, 15))
    assert api.get(f"{CANCEL}/{apidata.NIL}").status_code == 404
    assert api.get(f"{CANCEL}?productOrder.id=nope").status_code == 400
    assert set(api.get(f"{CANCEL}?fields=state").json()[0]) == {"id", "href", "@type", "state"}


def test_only_a_rejected_order_can_be_deleted(api, shop):
    offering, acc = shop
    spare = apidata.sellable_offering(api, name="Spare")  # prices start after today
    api.patch(spare["href"], {"isSellable": False})
    api.clock.set(at(OCT20))
    completed = apidata.order(api, acc, apidata.add_item(offering))
    response = api.delete(completed["href"])
    assert response.status_code == 409
    assert "only a rejected or draft order can be deleted" in response.json()["message"]
    rejected = apidata.order(api, acc, apidata.add_item(spare))
    assert rejected["state"] == "rejected"
    assert api.delete(spare["href"]).status_code == 409  # its order item refers to it
    assert api.delete(f"{rejected['href']}?force=1").status_code == 400
    assert api.delete(rejected["href"]).status_code == 204
    assert api.get(rejected["href"]).status_code == 404
    assert api.delete(rejected["href"]).status_code == 404
    # nothing refers to the offering any more: its price, then it, can go
    assert api.delete(spare["productOfferingPrice"][0]["href"]).status_code == 204
    assert api.delete(spare["href"]).status_code == 204
    assert api.delete(f"{BASE}/productOrder").status_code == 405


def test_a_cancellation_waits_for_a_billing_run_in_flight(api, shop, worker, migrated_db_url):
    """The cancellation locks the account FOR UPDATE, as bill_account does. Without that
    lock it reads no charge for November, takes the termination back, and the run then
    commits a charge that ends on the 15th - billing would see more due than charged."""
    offering, acc = shop
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    delete(api, acc, product, date(2026, 11, 15), on=at(OCT20))
    order = order_of(api, api.get(product["href"]).json(), "termination")
    api.clock.set(at(NOV1, 0))
    started = start_run(worker, NOV1, now=api.clock.now(), trigger="scheduled")
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            bill_account(first, UUID(acc["id"]), started.run, tz=TZ)
            api.clock.set(at(NOV1))
            result = _waits(lambda: api.post(CANCEL, cancel_body(order)))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 409, response.text
    assert "billing already charged" in response.json()["message"]


def test_a_cancellation_waits_for_the_account_lock_billing_holds(api, shop, migrated_db_url):
    """bill_account locks the account, computes, then writes. A charge already written
    holds the product (foreign key), but one still being computed holds only the account:
    without the cancellation's account lock it would take the termination back between
    billing's read and its write."""
    offering, acc = shop
    product = add(api, acc, offering, OCT2, on=at(OCT2))
    delete(api, acc, product, date(2026, 11, 15), on=at(OCT20))
    order = order_of(api, api.get(product["href"]).json(), "termination")
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            billing.load_account(first, UUID(acc["id"]))  # bill_account's first lock
            result = _waits(lambda: api.post(CANCEL, cancel_body(order)))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 201, response.text  # nothing was charged after all


def test_mcp_cancels_with_the_write_scope_only(api, mcp, shop):
    offering, acc = shop
    product = add(api, acc, offering, NOV1, on=at(OCT20))
    order = order_of(api, product, "creation")
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "cancel_order" not in mcp.tools(reader) and "list_cancellations" in mcp.tools(reader)
    response = mcp.call(reader, "cancel_order", {"body": {"productOrder": {"id": order["id"]}}})
    assert response.json()["error"] == "insufficient_scope"
    error, answer = mcp.result(
        writer, "cancel_order", {"body": {"productOrder": {"id": order["id"]}}}
    )
    assert not error and answer["status"] == 201
    assert api.get(order["href"]).json()["state"] == "cancelled"
