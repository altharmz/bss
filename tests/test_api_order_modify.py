"""TMF622 modify items: a product's offering changes from requestedStartDate on, billed
prorated (docs/decisions/tmf-api.md#modify-items-2026-10-18,
docs/decisions/billing-core.md#offering-changes-2026-10-18).

Offering A costs 300 a month, B 600; the account's cycle day is 1, so November (30 days)
bills A at 10.00 and B at 20.00 a day."""

from datetime import date
from decimal import Decimal

import pytest

from app.worker.process import bill_date
from tests import apidata
from tests.test_api_order_cancel import CANCEL, cancel_body, order_of, rates, refused, run, worker
from tests.test_billing_db import add, at, delete
from tests.test_events import subscribe, types
from tests.timeline import TZ, iso, midnight, z

__all__ = ["worker"]  # the fixture, imported for these tests

OCT2, OCT20, NOV1, NOV5, NOV6 = (
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 1),
    date(2026, 11, 5),
    date(2026, 11, 6),
)
NOV11, DEC1 = date(2026, 11, 11), date(2026, 12, 1)
INTERNAL = "/internal/v1/billingAccount"


@pytest.fixture
def shop(api):
    a = apidata.sellable_offering(api, value=300, name="Fiber 100")
    b = apidata.sellable_offering(api, value=600, name="Fiber 500")
    return a, b, apidata.account(api)


def modify_item(product: dict, offering: dict, item_id: str = "1", **over) -> dict:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "modify",
        "product": {"id": product["id"], "@type": "ProductRef"},
        "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
        **over,
    }


def modify(api, acc, product, offering, day: date | None, *, on) -> dict:
    api.clock.set(on)
    over = {} if day is None else {"requestedStartDate": iso(midnight(day))}
    return apidata.order(api, acc, modify_item(product, offering), **over)


def errors_of(order: dict) -> list[str]:
    return [e["code"] for e in order.get("productOrderErrorMessage", [])]


def started(api, shop) -> dict:
    a, _, acc = shop
    return add(api, acc, a, OCT2, on=at(date(2026, 10, 1)))


# --- shape and rejections ---


def test_a_modify_item_needs_product_and_offering(api, shop):
    a, b, acc = shop
    product = started(api, shop)
    no_offering = {k: v for k, v in modify_item(product, b).items() if k != "productOffering"}
    no_product = {k: v for k, v in modify_item(product, b).items() if k != "product"}
    for item in (no_offering, no_product):
        response = api.post(apidata.ORDER, apidata.order_body(acc, item))
        assert response.status_code == 422, response.text
        assert "needs product and either productOffering" in response.json()["message"]


def test_rejections(api, shop):
    a, b, acc = shop
    euro = apidata.sellable_offering(api, value=10, unit="EUR", name="Euro")
    product = started(api, shop)
    other = apidata.account(api, name="Other")
    theirs = add(api, other, a, OCT2, on=at(date(2026, 10, 1)))
    later = add(api, acc, a, NOV1, on=at(OCT20))
    on = at(OCT20)
    assert errors_of(modify(api, acc, theirs, b, NOV1, on=on)) == ["productOfOtherAccount"]
    assert errors_of(modify(api, acc, product, a, NOV1, on=on)) == ["offeringUnchanged"]
    assert errors_of(modify(api, acc, later, b, date(2026, 10, 25), on=on)) == ["productNotStarted"]
    assert errors_of(modify(api, acc, product, euro, NOV1, on=on)) == ["noRecurringPrice"]
    assert modify(api, acc, product, b, NOV11, on=on)["state"] == "completed"
    # one change waits at a time, and one per day
    assert errors_of(modify(api, acc, product, a, DEC1, on=on)) == ["openModifyItem"]
    assert modify(api, acc, later, b, NOV1, on=on)["state"] == "completed"  # per product


def test_a_change_must_come_before_the_termination(api, shop):
    a, b, acc = shop
    product = started(api, shop)
    delete(api, acc, product, NOV11, on=at(OCT20))
    assert errors_of(modify(api, acc, product, b, NOV11, on=at(OCT20))) == ["productAlreadyEnded"]
    assert modify(api, acc, product, b, NOV5, on=at(OCT20))["state"] == "completed"
    ended = add(api, acc, a, OCT20, on=at(OCT20))
    delete(api, acc, ended, None, on=at(OCT20))
    assert errors_of(modify(api, acc, ended, b, None, on=at(OCT20))) == ["productAlreadyEnded"]


def test_a_delete_may_not_end_the_product_on_or_before_a_pending_change(api, shop):
    a, b, acc = shop
    product = started(api, shop)
    modify(api, acc, product, b, NOV11, on=at(OCT20))
    for day in (NOV5, NOV11):
        placed = apidata.order(
            api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(day))
        )
        assert errors_of(placed) == ["openModifyItem"]
        assert "cancel that order" in placed["productOrderErrorMessage"][0]["reason"]
    delete(api, acc, product, date(2026, 11, 12), on=at(OCT20))


# --- execution, TMF637, billing ---


def test_a_change_after_the_month_was_charged_is_billed_prorated(api, shop, worker):
    a, b, acc = shop
    product = started(api, shop)
    run(worker, api, NOV1)
    placed = modify(api, acc, product, b, NOV11, on=at(NOV5))
    assert placed["state"] == "completed"
    (item,) = placed["productOrderItem"]
    assert item["product"]["id"] == product["id"]
    assert item["itemPrice"][0]["price"]["dutyFreeAmount"]["value"] == 600
    before = api.get(product["href"]).json()
    assert before["productOffering"]["id"] == a["id"] and before["name"] == "Fiber 100"
    assert [r["role"] for r in before["productOrderItem"]] == ["creation", "modification"]
    assert before["productOrderItem"][1]["orderItemAction"] == "modify"
    assert before["status"] == "active"

    run(worker, api, NOV6)  # A keeps 10 days: 100.00 of 300.00
    assert (z(midnight(NOV11)), Decimal("-200.00")) in rates(api, product)
    assert (z(midnight(NOV11)), Decimal("400.00")) not in rates(api, product)
    run(worker, api, NOV11)  # B from the 11th: 20 days of 600/30
    assert (z(midnight(NOV11)), Decimal("400.00")) in rates(api, product)
    after = api.get(product["href"]).json()
    # the name is the product's own (its offering's at creation): the offering changed
    assert after["productOffering"]["id"] == b["id"] and after["name"] == "Fiber 100"
    assert after["productOffering"]["name"] == "Fiber 500"
    assert after["productPrice"][0]["price"]["dutyFreeAmount"]["value"] == 600
    names = {
        r["appliedBillingRateType"]: r["product"]["name"]
        for r in api.get(f"{apidata.RATE}?product.id={product['id']}").json()
        if r["periodCoverage"]["startDateTime"] == z(midnight(NOV11))
    }
    assert names == {"appliedBillingCredit": "Fiber 100", "appliedBillingCharge": "Fiber 500"}
    listed = api.get(f"{INTERNAL}/{acc['id']}/products").json()
    assert [p["productOffering"]["id"] for p in listed] == [b["id"]]
    run(worker, api, DEC1)
    assert (z(midnight(DEC1)), Decimal("600.00")) in rates(api, product)


def test_a_change_placed_on_a_day_is_first_seen_by_the_next_days_run(api, shop, worker):
    a, b, acc = shop
    product = started(api, shop)
    run(worker, api, NOV1)
    modify(api, acc, product, b, NOV11, on=at(NOV5, 10))
    api.clock.set(at(NOV5, 11))  # the run of the 5th, late or retried: still before
    bill_date(worker, NOV5, clock=api.clock, tz=TZ, trigger="catchUp")
    assert all(amount > 0 for _, amount in rates(api, product))
    run(worker, api, NOV6)
    assert (z(midnight(NOV11)), Decimal("-200.00")) in rates(api, product)


def test_a_change_known_before_the_month_gives_two_reduced_charges(api, shop, worker):
    a, b, acc = shop
    product = started(api, shop)
    modify(api, acc, product, b, NOV11, on=at(OCT20))
    run(worker, api, NOV1)
    run(worker, api, NOV11)
    november = [r for r in rates(api, product) if r[0] >= z(midnight(NOV1))]
    assert sorted(november) == [
        (z(midnight(NOV1)), Decimal("100.00")),
        (z(midnight(NOV11)), Decimal("400.00")),
    ]


def test_a_change_today_is_announced_by_the_order_a_later_one_by_the_run(
    api, shop, worker, db_conn
):
    a, b, acc = shop
    product = started(api, shop)
    subscribe(api, "TMF637", "ProductAttributeValueChangeEvent")
    modify(api, acc, product, b, None, on=at(OCT20))  # today
    assert types(db_conn, "TMF637") == ["ProductAttributeValueChangeEvent"]
    assert api.get(product["href"]).json()["productOffering"]["id"] == b["id"]
    modify(api, acc, product, a, NOV11, on=at(OCT20))
    run(worker, api, NOV1)
    assert len(types(db_conn, "TMF637")) == 1
    run(worker, api, NOV11)
    run(worker, api, NOV11)  # a second call of the same date announces nothing again
    assert len(types(db_conn, "TMF637")) == 2


# --- cancelProductOrder ---


def test_a_pending_change_is_cancelled_while_billing_has_not_reflected_it(api, shop, worker):
    a, b, acc = shop
    product = started(api, shop)
    run(worker, api, NOV1)  # November charged in full for A
    modify(api, acc, product, b, NOV11, on=at(NOV5))
    order = order_of(api, api.get(product["href"]).json(), "modification")
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert api.get(order["href"]).json()["productOrderItem"][0]["state"] == "cancelled"
    restored = api.get(product["href"]).json()
    assert [r["role"] for r in restored["productOrderItem"]] == ["creation"]
    run(worker, api, NOV11)
    assert [r for r in rates(api, product) if r[0] >= z(midnight(NOV1))] == [
        (z(midnight(NOV1)), Decimal("300.00"))
    ]
    # the product can be changed again
    assert modify(api, acc, product, b, DEC1, on=at(NOV11))["state"] == "completed"


def test_a_change_billing_reflects_stays(api, shop, worker):
    a, b, acc = shop
    product = started(api, shop)
    known_early = add(api, acc, a, OCT20, on=at(OCT20))
    modify(api, acc, known_early, b, NOV11, on=at(OCT20))
    run(worker, api, NOV1)  # known_early: A charged for [1st, 11th) only
    modify(api, acc, product, b, NOV11, on=at(NOV5))
    early = order_of(api, api.get(known_early["href"]).json(), "modification")
    refused(api, early, "item 1 (modify)", "billing already reflects the offering change")
    run(worker, api, NOV6)  # product: A refunded from the 11th
    order = order_of(api, api.get(product["href"]).json(), "modification")
    refused(api, order, "billing already reflects the offering change on 2026-11-11")


def test_a_change_that_took_effect_stays(api, shop):
    a, b, acc = shop
    product = started(api, shop)
    modify(api, acc, product, b, None, on=at(OCT20))
    order = order_of(api, api.get(product["href"]).json(), "modification")
    refused(api, order, "the offering change took effect on 2026-10-20")


def test_an_add_whose_product_another_order_changes_stays(api, shop):
    a, b, acc = shop
    product = add(api, acc, a, NOV1, on=at(OCT20))
    modify(api, acc, product, b, NOV1, on=at(OCT20))
    refused(api, order_of(api, product, "creation"), "another order changes")
