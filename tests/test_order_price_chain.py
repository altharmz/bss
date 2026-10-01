"""D-W20: an add order is rejected (priceEnds) unless, from the product's start date, the
offering's recurring prices in the account currency form a gapless chain that ends in an
open-ended price. The whole chain is checked, not just the price at the start. Together
with D-W18 this keeps the invariant "a live product has a price for every future day".

The prices are set up before any product exists (ending a price is then free)."""

from datetime import date, datetime, timedelta

import pytest

from tests import apidata
from tests.apidata import CATALOG, ORDER
from tests.timeline import START, iso, midnight

PRICES = f"{CATALOG}/productOfferingPrice"


def d(month: int, day: int) -> datetime:
    return midnight(date(2026, month, day))


def offering_with_prices(api, *periods: tuple[datetime, datetime | None]) -> dict:
    """An Active offering whose recurring RUB prices cover exactly `periods` (start, end);
    the first one starts at START."""
    offering = apidata.sellable_offering(api, value=300)
    first = offering["productOfferingPrice"][0]
    refs = [{"id": first["id"], "@type": "ProductOfferingPriceRef"}]
    (_, first_end), *rest = periods
    if first_end is not None:
        period = {"startDateTime": iso(START), "endDateTime": iso(first_end)}
        assert api.patch(first["href"], {"validFor": period}).status_code == 200
    for start, end in rest:
        price = api.post(
            PRICES,
            {
                "@type": "ProductOfferingPrice",
                "name": "Monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "RUB", "value": 310},
                "validFor": {"startDateTime": iso(start)}
                | ({"endDateTime": iso(end)} if end else {}),
            },
        ).json()
        refs.append({"id": price["id"], "@type": "ProductOfferingPriceRef"})
    assert api.patch(offering["href"], {"productOfferingPrice": refs}).status_code == 200
    return offering


def order(api, offering: dict, start: date) -> dict:
    account = apidata.account(api)
    api.clock.set(d(10, 2) + timedelta(hours=10))
    response = api.post(
        ORDER,
        apidata.order_body(
            account, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def rejection(placed: dict) -> tuple[str, str] | None:
    if placed["state"] == "completed":
        return None
    (message,) = placed["productOrderErrorMessage"]
    return message["code"], message["reason"]


def test_an_open_ended_price_is_enough(api):
    offering = offering_with_prices(api, (START, None))
    assert rejection(order(api, offering, date(2026, 10, 2))) is None


def test_an_exact_chain_ending_open_ended_is_accepted(api):
    offering = offering_with_prices(api, (START, d(11, 1)), (d(11, 1), d(12, 1)), (d(12, 1), None))
    assert rejection(order(api, offering, date(2026, 10, 2))) is None


def test_a_gap_later_in_the_chain_is_rejected(api):
    # the price at the start is fine; the gap is two links later (Dec 1 .. Dec 15)
    offering = offering_with_prices(api, (START, d(11, 1)), (d(11, 1), d(12, 1)), (d(12, 15), None))
    assert rejection(order(api, offering, date(2026, 10, 2))) == (
        "priceEnds",
        "the recurring price in RUB ends at 2026-12-01 00:00 and no price follows it",
    )


def test_an_ended_last_price_is_rejected(api):
    offering = offering_with_prices(api, (START, d(11, 1)), (d(11, 1), d(12, 1)))
    assert rejection(order(api, offering, date(2026, 10, 2))) == (
        "priceEnds",
        "the recurring price in RUB ends at 2026-12-01 00:00 and no price follows it",
    )


def test_a_gap_before_the_start_does_not_matter(api):
    offering = offering_with_prices(api, (START, d(11, 1)), (d(11, 15), None))
    assert rejection(order(api, offering, date(2026, 11, 20))) is None
    # ...but starting before the gap does
    assert rejection(order(api, offering, date(2026, 10, 5)))[0] == "priceEnds"


@pytest.mark.parametrize("start", [date(2026, 10, 2), date(2026, 11, 20)])
def test_a_rejected_order_creates_no_product(api, start):
    offering = offering_with_prices(api, (START, d(11, 1)))
    placed = order(api, offering, start)
    assert placed["state"] == "rejected"
    assert all("product" not in item for item in placed["productOrderItem"])
