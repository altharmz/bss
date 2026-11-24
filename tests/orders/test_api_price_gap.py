"""D-W18: ending (or moving the end of) a recurring price must not leave a product without
a price. Billing prices every period at its first billable day's midnight, and a price
can never start in the past, so a gap could never be filled afterwards.

Ending is a 409 unless a price of the same offering, type and currency starts exactly at
the new end, or every product of the offering in that currency is terminated by the first
day the old price no longer covers (its termination_date is exclusive).
"""

from datetime import date, datetime, timedelta

import pytest

from app.api import catalog as catalog_api
from tests import apidata
from tests.apidata import CATALOG
from tests.timeline import START, iso, midnight

PRICES = f"{CATALOG}/productOfferingPrice"


def end_price(api, price_href: str, end: datetime | None):
    period = {"startDateTime": iso(START)} | ({"endDateTime": iso(end)} if end else {})
    return api.patch(price_href, {"validFor": period})


def order_product(api, account, offering, start: date) -> dict:
    placed = apidata.order(
        api, account, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def terminate(api, account, product, on: date) -> None:
    placed = apidata.order(
        api, account, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(on))
    )
    assert placed["state"] == "completed", placed


@pytest.fixture
def live(api):
    """A 300.00 USD offering with one open-ended product on a USD account."""
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    api.clock.set(midnight(date(2026, 10, 2)) + timedelta(hours=10))
    product = order_product(api, account, offering, date(2026, 10, 2))
    price_href = offering["productOfferingPrice"][0]["href"]
    return offering, account, product, price_href


def test_ending_the_price_of_a_live_product_is_refused(api, live):
    _, _, _, price_href = live
    response = end_price(api, price_href, midnight(date(2026, 11, 1)))
    assert response.status_code == 409
    assert response.json()["message"] == (
        "validFor.endDateTime would leave 1 product(s) of this offering in USD without a"
        " price from 2026-11-01: terminate them by then, or attach a price that starts"
        " exactly at the new end"
    )
    assert "endDateTime" not in api.get(price_href).json()["validFor"]  # unchanged


def test_the_refusal_counts_unpriced_products_only_up_to_the_cap(api, live, monkeypatch):
    """#331: the refusal does not count every product of a large offering; beyond
    GAP_COUNT_CAP it says "more than N", up to it the exact number."""
    offering, account, _, price_href = live
    monkeypatch.setattr(catalog_api, "GAP_COUNT_CAP", 2)
    order_product(api, account, offering, date(2026, 10, 2))
    exact = end_price(api, price_href, midnight(date(2026, 11, 1)))
    assert exact.status_code == 409
    assert "would leave 2 product(s) of this offering" in exact.json()["message"]
    order_product(api, account, offering, date(2026, 10, 2))
    capped = end_price(api, price_href, midnight(date(2026, 11, 1)))
    assert capped.status_code == 409
    assert "would leave more than 2 product(s) of this offering" in capped.json()["message"]


@pytest.mark.parametrize(
    ("end", "termination", "allowed"),
    [
        # the price covers every midnight before its end: a midnight end leaves that day
        (midnight(date(2026, 11, 1)), date(2026, 10, 20), True),
        (midnight(date(2026, 11, 1)), date(2026, 11, 1), True),  # boundary: not billed then
        (midnight(date(2026, 11, 1)), date(2026, 11, 2), False),
        # a mid-day end still covers that day's midnight: the first uncovered day is next
        (midnight(date(2026, 11, 1)) + timedelta(hours=12), date(2026, 11, 2), True),
        (midnight(date(2026, 11, 1)) + timedelta(hours=12), date(2026, 11, 3), False),
    ],
)
def test_products_terminated_by_the_first_uncovered_day_allow_the_end(
    api, live, end, termination, allowed
):
    _, account, product, price_href = live
    terminate(api, account, product, termination)
    response = end_price(api, price_href, end)
    assert response.status_code == (200 if allowed else 409), response.text


def test_a_cancelled_product_needs_no_price(api, live):
    offering, account, product, price_href = live
    future = order_product(api, account, offering, date(2026, 12, 15))
    terminate(api, account, future, date(2026, 10, 2))  # before its start: cancelled
    terminate(api, account, product, date(2026, 10, 20))
    assert api.get(future["href"]).json()["status"] == "cancelled"
    assert end_price(api, price_href, midnight(date(2026, 11, 1))).status_code == 200


def test_a_gap_before_a_later_successor_is_refused_an_exact_successor_is_not(api):
    """The old price ends Dec 1, the next one starts Dec 15 (set up before any product).
    A product starting Dec 20 is accepted (ordering only needs a gapless chain from its
    start, D-W20). Moving the old end to Dec 10 still leaves a gap and is refused; moving
    it to exactly where the successor starts closes the gap and is allowed."""
    offering = apidata.sellable_offering(api, value=300)
    old_href = offering["productOfferingPrice"][0]["href"]
    assert end_price(api, old_href, midnight(date(2026, 12, 1))).status_code == 200
    successor = api.post(
        PRICES,
        {
            "@type": "ProductOfferingPrice",
            "name": "Monthly from mid-December",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 330},
            "validFor": {"startDateTime": iso(midnight(date(2026, 12, 15)))},
        },
    ).json()
    refs = [
        {"id": offering["productOfferingPrice"][0]["id"], "@type": "ProductOfferingPriceRef"},
        {"id": successor["id"], "@type": "ProductOfferingPriceRef"},
    ]
    assert api.patch(offering["href"], {"productOfferingPrice": refs}).status_code == 200

    account = apidata.account(api)
    api.clock.set(midnight(date(2026, 10, 2)) + timedelta(hours=10))
    order_product(api, account, offering, date(2026, 12, 20))

    still_a_gap = end_price(api, old_href, midnight(date(2026, 12, 10)))
    assert still_a_gap.status_code == 409
    assert "without a price from 2026-12-10" in still_a_gap.json()["message"]
    closed = end_price(api, old_href, midnight(date(2026, 12, 15)))  # exactly the successor
    assert closed.status_code == 200, closed.text


def test_an_unattached_price_can_always_end(api):
    price = api.post(
        PRICES,
        {
            "@type": "ProductOfferingPrice",
            "name": "Loose",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 1},
            "validFor": {"startDateTime": iso(START)},
        },
    ).json()
    assert end_price(api, price["href"], midnight(date(2026, 11, 1))).status_code == 200
