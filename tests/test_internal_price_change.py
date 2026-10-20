"""POST /internal/v1/productOffering/{id}/priceChange (D-W21): change a live offering's
price in one transaction - end the current price at midnight of validFrom, create and
attach the successor from that moment. Every response is checked against
spec/internal/bss-internal.oas.yaml by the client."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.internal import catalog as price_change_module
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import CATALOG
from tests.timeline import iso, midnight

BASE = "/internal/v1/productOffering"


def change(api, offering_id: str, **over):
    body = {"amount": 330.5, "currency": "USD", "validFrom": "2026-11-01"} | over
    return api.post(f"{BASE}/{offering_id}/priceChange", body)


@pytest.fixture
def live(api):
    """A 300.00 USD offering with one open-ended product; today is 2026-10-02."""
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    api.clock.set(midnight(date(2026, 10, 2)) + timedelta(hours=10))
    placed = apidata.order(api, account, apidata.add_item(offering))
    assert placed["state"] == "completed"
    return offering, account


def test_tmf620_cannot_change_a_live_price_in_either_order(api, live):
    """Why the endpoint exists: with a product live, ending first leaves it without a
    price (D-W18), attaching first overlaps the open price (no-overlap constraint)."""
    offering, _ = live
    old = offering["productOfferingPrice"][0]
    switch = iso(midnight(date(2026, 11, 1)))
    end_first = api.patch(
        old["href"],
        {"validFor": {"startDateTime": apidata.START, "endDateTime": switch}},
    )
    assert end_first.status_code == 409
    successor = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "New",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 330},
            "validFor": {"startDateTime": switch},
        },
    ).json()
    refs = [
        {"id": old["id"], "@type": "ProductOfferingPriceRef"},
        {"id": successor["id"], "@type": "ProductOfferingPriceRef"},
    ]
    assert api.patch(offering["href"], {"productOfferingPrice": refs}).status_code == 409


def test_a_live_price_changes_in_one_step(api, live):
    offering, account = live
    response = change(api, offering["id"])
    assert response.status_code == 201, response.text
    body = decimals(response)
    assert body["endedPrice"]["endsOn"] == "2026-11-01"
    assert body["newPrice"]["price"] == {"unit": "USD", "value": Decimal("330.50")}
    assert body["newPrice"]["validFrom"] == "2026-11-01"
    assert response.headers["Location"] == body["newPrice"]["href"]

    # the old price ends exactly where the new one starts: midnight of Nov 1 in Moscow
    old = api.get(body["endedPrice"]["href"]).json()
    new = api.get(body["newPrice"]["href"]).json()
    assert old["validFor"]["endDateTime"] == new["validFor"]["startDateTime"]
    assert new["validFor"]["startDateTime"] == "2026-10-31T21:00:00Z"
    assert "endDateTime" not in new["validFor"]
    attached = api.get(offering["href"]).json()["productOfferingPrice"]
    assert {p["id"] for p in attached} == {body["endedPrice"]["id"], body["newPrice"]["id"]}

    # the chain is gapless: new orders are accepted, and priced at the new price from
    # the switch (the backend's own preview)
    later = apidata.order(
        api,
        account,
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(date(2026, 11, 5))),
    )
    assert later["state"] == "completed"
    preview = decimals(
        api.get(
            f"/internal/v1/billingAccount/{account['id']}/firstCharge"
            f"?productOffering.id={offering['id']}&startDate=2026-11-05"
        )
    )
    assert preview["price"]["value"] == Decimal("330.50")


def test_the_new_price_can_be_changed_again_later(api, live):
    offering, _ = live
    assert change(api, offering["id"]).status_code == 201
    again = change(api, offering["id"], amount=350, validFrom="2026-12-01")
    assert again.status_code == 201, again.text
    # but not from a date on or before the price that is already scheduled
    clash = change(api, offering["id"], amount=360, validFrom="2026-11-20")
    assert clash.status_code == 409
    assert "already starts on or after 2026-11-20" in clash.json()["message"]


@pytest.mark.parametrize(
    ("over", "status", "message"),
    [
        ({"validFrom": "2026-10-02"}, 422, "validFrom must be 2026-10-03 or later"),  # today
        ({"validFrom": "2026-09-30"}, 422, "validFrom must be 2026-10-03 or later"),
        ({"validFrom": "2026-11-1"}, 422, None),
        ({"validFrom": "2026-11-01T00:00:00+03:00"}, 422, None),
        ({"amount": 1.005}, 422, None),  # never rounded
        ({"amount": -1}, 422, "amount must not be negative"),
        ({"currency": "usd"}, 422, None),
        ({"currency": "EUR"}, 409, "no open-ended recurring price in EUR"),
        ({"extra": 1}, 400, None),
    ],
)
def test_refusals(api, live, over, status, message):
    offering, _ = live
    response = change(api, offering["id"], **over)
    assert response.status_code == status, response.text
    if message:
        assert message in response.json()["message"]


def test_unknown_offering_and_wrong_content_type(api):
    assert change(api, apidata.NIL).status_code == 404
    response = api.post_raw(
        f"{BASE}/{apidata.NIL}/priceChange",
        '{"amount": 1, "currency": "USD", "validFrom": "2026-11-01"}',
        content_type="text/plain",
    )
    assert response.status_code == 415


def test_a_failure_half_way_changes_nothing(api, live, monkeypatch):
    """Atomicity: the old price is ended first; if creating the successor then fails, the
    end is rolled back with it - the offering keeps its open-ended price."""
    offering, _ = live

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(price_change_module.catalog, "create_product_offering_price", explode)
    response = change(api, offering["id"])
    assert response.status_code == 500
    old = api.get(offering["productOfferingPrice"][0]["href"]).json()
    assert "endDateTime" not in old["validFor"]  # not ended
    assert len(api.get(offering["href"]).json()["productOfferingPrice"]) == 1  # nothing new
