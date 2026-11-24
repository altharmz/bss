"""TMF charge intervals persist and reach billing, quotes, previews and refunds."""

from datetime import date
from decimal import Decimal

import pytest

from tests import apidata
from tests.builders.billing.billing_db import add, at, bill, delete
from tests.builders.catalog.api_catalog import make_offering, make_price, price_refs
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body, order_of
from tests.builders.orders.api_order_modify import errors_of, modify
from tests.timeline import iso, midnight

INTERNAL = "/internal/v1/billingAccount"
START = date(2026, 10, 15)


def offering(api, unit, length=1, amount=100):
    price = make_price(
        api,
        recurringChargePeriodType=unit,
        recurringChargePeriodLength=length,
        price={"unit": "USD", "value": amount},
    )
    return make_offering(api, productOfferingPrice=price_refs(price)), price


@pytest.mark.parametrize(
    "unit,length,end,label",
    [
        ("daily", 1, "2026-10-16", "day"),
        ("daily", 3, "2026-10-18", "3 days"),
        ("weekly", 1, "2026-10-22", "week"),
        ("weekly", 2, "2026-10-29", "2 weeks"),
        ("monthly", 3, "2027-01-15", "3 months"),
        ("yearly", 1, "2027-10-15", "year"),
        ("yearly", 2, "2028-10-15", "2 years"),
    ],
)
def test_interval_reaches_the_worker_and_every_read_view(api, db_conn, unit, length, end, label):
    offer, price = offering(api, unit, length)
    assert api.get(price["href"]).json()["recurringChargePeriodLength"] == length
    acc = apidata.account(api)
    preview_response = api.get(
        f"{INTERNAL}/{acc['id']}/firstCharge?productOffering.id={offer['id']}&startDate={START}"
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["period"] == {"start": str(START), "end": end}
    assert preview["price"] == {"unit": "USD", "value": 100}
    assert preview["amount"] == {"unit": "USD", "value": 100}
    assert preview["prorated"] is False
    assert preview["next"]["start"] == end
    assert preview["recurringChargePeriod"] == label
    p = add(api, acc, offer, START, on=at(date(2026, 10, 14)))
    assert p["productPrice"][0]["recurringChargePeriod"] == label
    order = api.get(order_of(api, p, "creation")["href"]).json()
    assert order["productOrderItem"][0]["itemPrice"][0]["recurringChargePeriod"] == label
    assert order["orderTotalPrice"][0]["recurringChargePeriod"] == label
    [charge] = bill(db_conn, acc, START).abrs
    assert charge.amount == Decimal("100.00")
    assert str(charge.period.end) == end
    assert bill(db_conn, acc, START).is_empty
    api.clock.set(at(START))
    [read] = api.get(f"{INTERNAL}/{acc['id']}/products").json()
    assert read["nextChargeDate"] == end
    assert read["recurringChargePeriod"] == label
    [read_charge] = api.get(f"{INTERNAL}/{acc['id']}/charges").json()
    assert read_charge["periodDays"] == (date.fromisoformat(end) - START).days


def test_yearly_termination_refund_and_cancellation_guard(api, db_conn):
    offer, _ = offering(api, "yearly", amount=365)
    acc = apidata.account(api)
    p = add(api, acc, offer, START, on=at(date(2026, 10, 14)))
    bill(db_conn, acc, START)
    ends = date(2026, 11, 15)
    delete(api, acc, p, ends, on=at(date(2026, 10, 16)))
    p = api.get(p["href"]).json()
    deletion = order_of(api, p, "termination")
    refused = api.post(CANCEL, cancel_body(deletion))
    assert refused.status_code == 409, refused.text
    [refund] = bill(db_conn, acc, date(2026, 10, 17)).abrs
    assert refund.amount == Decimal("-334.00")  # 31 used business dates
    assert refund.period.end == date(2027, 10, 15)
    assert bill(db_conn, acc, date(2026, 10, 17)).is_empty
    charges = api.get(f"{INTERNAL}/{acc['id']}/charges").json()
    assert all(a["periodDays"] == 365 for a in charges)


def test_cross_cadence_offering_change_and_one_refund_rule(api, db_conn):
    yearly, _ = offering(api, "yearly", amount=365)
    weekly, _ = offering(api, "weekly", amount=7)
    acc = apidata.account(api)
    p = add(api, acc, yearly, START, on=at(date(2026, 10, 14)))
    bill(db_conn, acc, START)
    changed = modify(api, acc, p, weekly, date(2026, 10, 25), on=at(date(2026, 10, 20)))
    assert changed["state"] == "completed", changed
    result = bill(db_conn, acc, date(2026, 10, 25))
    assert [(a.kind, a.amount) for a in result.abrs] == [
        ("refund", Decimal("-355.00")),
        ("recurring", Decimal("7.00")),
    ]
    api.clock.set(at(date(2026, 10, 25)))
    [read] = api.get(f"{INTERNAL}/{acc['id']}/products").json()
    assert read["nextChargeDate"] == "2026-11-01"


def test_successor_preserves_interval_and_cannot_reinterpret_a_live_price(api):
    offer, price = offering(api, "weekly", 2)
    path = f"/internal/v1/productOffering/{offer['id']}/priceChange"
    response = api.post(path, {"amount": 120, "currency": "USD", "validFrom": "2026-11-01"})
    assert response.status_code == 201, response.text
    new = api.get(response.json()["newPrice"]["href"]).json()
    assert (new["recurringChargePeriodType"], new["recurringChargePeriodLength"]) == ("weekly", 2)
    wrong = make_price(
        api,
        recurringChargePeriodType="yearly",
        validFor={"startDateTime": iso(midnight(date(2027, 1, 1)))},
    )
    response = api.patch(offer["href"], {"productOfferingPrice": price_refs(price, new, wrong)})
    assert response.status_code == 409, response.text
    assert "interval" in response.json()["message"]


def test_patch_interval_on_unattached_price_and_reject_invalid_lengths(api):
    price = make_price(api)
    patched = api.patch(
        price["href"], {"recurringChargePeriodType": "weekly", "recurringChargePeriodLength": 2}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["recurringChargePeriodLength"] == 2
    for length in [0, -1, True, "2", None, 1001]:
        response = api.patch(price["href"], {"recurringChargePeriodLength": length})
        assert response.status_code == 422, response.text


def test_known_yearly_termination_blocks_a_second_cut_in_the_same_charge_period(api, db_conn):
    yearly, _ = offering(api, "yearly", amount=365)
    weekly, _ = offering(api, "weekly", amount=7)
    acc = apidata.account(api)
    p = add(api, acc, yearly, START, on=at(date(2026, 10, 14)))
    bill(db_conn, acc, START)
    delete(api, acc, p, date(2027, 5, 15), on=at(date(2026, 10, 16)))
    bill(db_conn, acc, date(2026, 10, 17))
    changed = modify(api, acc, p, weekly, date(2027, 4, 15), on=at(date(2026, 10, 20)))
    assert errors_of(changed) == ["terminationInPeriod"]


def test_mixed_interval_order_totals_keep_the_intervals_separate(api):
    monthly, _ = offering(api, "monthly", amount=100)
    weekly, _ = offering(api, "weekly", amount=7)
    acc = apidata.account(api)
    placed = apidata.order(
        api,
        acc,
        apidata.add_item(monthly),
        apidata.add_item(weekly, "2"),
        requestedStartDate=iso(midnight(START)),
    )
    assert placed["state"] == "completed", placed
    assert [
        (p["recurringChargePeriod"], p["price"]["dutyFreeAmount"]["value"])
        for p in placed["orderTotalPrice"]
    ] == [("month", 100), ("week", 7)]
