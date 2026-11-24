"""An offering's validFor through PATCH (doc-findings #8). It decides which start dates
new orders may have (offeringNotValid), so it may not be rewritten in the past: the start
cannot move into the past and the end cannot be set or moved into the past, both 409
with nothing in the patch applied. Repeating the current value is not a change.

Billing never reads the offering's validFor - only its prices - so ending an offering
with a live product leaves the product priced: the price rules (D-W18 ending a price,
D-W20 ordering) are what keep "a live product has a price for every future day"."""

from datetime import date, timedelta

import pytest

from tests import apidata
from tests.timeline import NOW, START, iso, midnight

LATER = midnight(date(2026, 10, 5)) + timedelta(hours=12)  # after the offering started

PAST_START = (
    "validFor.startDateTime cannot be moved into the past; only to the present or the future"
)
PAST_END = "validFor.endDateTime cannot be set into the past; only to the present or the future"


def period(start, end=None) -> dict:
    return {"startDateTime": iso(start)} | ({"endDateTime": iso(end)} if end else {})


@pytest.fixture
def offering(api) -> dict:
    return apidata.sellable_offering(api, value=300)


def test_the_start_cannot_move_into_the_past(api, offering):
    api.clock.set(LATER)
    for start in (midnight(date(2026, 10, 3)), midnight(date(2026, 9, 1))):
        response = api.patch(offering["href"], {"validFor": period(start), "name": "Moved"})
        assert response.status_code == 409, response.text
        assert response.json()["message"] == PAST_START
    unchanged = api.get(offering["href"]).json()
    assert (unchanged["validFor"], unchanged["name"]) == (offering["validFor"], offering["name"])


def test_a_start_that_has_passed_may_be_repeated_or_moved_forward(api, offering):
    api.clock.set(LATER)
    end = midnight(date(2026, 12, 1))
    repeated = api.patch(offering["href"], {"validFor": period(START, end)})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["validFor"]["startDateTime"] == offering["validFor"]["startDateTime"]
    moved = api.patch(offering["href"], {"validFor": period(midnight(date(2026, 10, 10)), end)})
    assert moved.status_code == 200, moved.text


def test_a_future_start_may_move_to_now_but_not_before(api, offering):
    before = api.patch(offering["href"], {"validFor": period(NOW - timedelta(seconds=1))})
    assert before.status_code == 409
    assert before.json()["message"] == PAST_START
    assert api.patch(offering["href"], {"validFor": period(NOW)}).status_code == 200


def test_the_end_cannot_be_set_into_the_past(api, offering):
    api.clock.set(LATER)
    past = api.patch(offering["href"], {"validFor": period(START, LATER - timedelta(seconds=1))})
    assert past.status_code == 409
    assert past.json()["message"] == PAST_END
    assert "endDateTime" not in api.get(offering["href"]).json()["validFor"]
    assert api.patch(offering["href"], {"validFor": period(START, LATER)}).status_code == 200


def test_a_future_end_cannot_be_moved_into_the_past_but_may_be_repeated(api, offering):
    end = midnight(date(2026, 10, 20))
    assert api.patch(offering["href"], {"validFor": period(START, end)}).status_code == 200
    api.clock.set(LATER)
    moved = api.patch(offering["href"], {"validFor": period(START, midnight(date(2026, 10, 4)))})
    assert moved.status_code == 409
    assert moved.json()["message"] == PAST_END
    assert api.patch(offering["href"], {"validFor": period(START, end)}).status_code == 200
    api.clock.set(midnight(date(2026, 10, 25)))  # the end has passed: repeating it is fine
    assert api.patch(offering["href"], {"validFor": period(START, end)}).status_code == 200


def test_ending_an_offering_leaves_its_live_products_priced(api, offering):
    """No price gap: billing prices from the offering's prices, never its validFor. After
    the offering ended the product is still priced, its price still cannot end (D-W18),
    and new orders after the end are rejected offeringNotValid."""
    account = apidata.account(api)
    api.clock.set(midnight(date(2026, 10, 2)) + timedelta(hours=10))
    placed = apidata.order(
        api,
        account,
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(date(2026, 10, 2))),
    )
    assert placed["state"] == "completed", placed
    offering_end = midnight(date(2026, 10, 10))
    ended = api.patch(offering["href"], {"validFor": period(START, offering_end)})
    assert ended.status_code == 200, ended.text

    price_href = offering["productOfferingPrice"][0]["href"]
    price_end = api.patch(price_href, {"validFor": period(START, midnight(date(2026, 11, 1)))})
    assert price_end.status_code == 409  # the product is still live: D-W18 holds

    api.clock.set(midnight(date(2026, 11, 15)) + timedelta(hours=10))
    products = api.get(f"/internal/v1/billingAccount/{account['id']}/products").json()
    assert [(p["status"], p["price"]) for p in products] == [
        ("active", {"unit": "USD", "value": 300})
    ]
    late = apidata.order(api, account, apidata.add_item(offering))
    assert late["state"] == "rejected"
    codes = [m["code"] for m in late["productOrderErrorMessage"]]
    assert codes == ["offeringNotValid"]
