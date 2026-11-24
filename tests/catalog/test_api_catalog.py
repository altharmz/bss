"""TMF620 Product Catalog: specifications, offerings, prices and how prices attach."""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests import timeline
from tests.apiclient import decimals
from tests.builders.catalog.api_catalog import (
    NIL,
    OFFERING,
    PRICE,
    START,
    make_offering,
    make_price,
    make_spec,
    offering_body,
    price_body,
    price_refs,
)
from tests.timeline import NOW, iso, z

pytestmark = pytest.mark.smoke


def test_offering_references_its_specification(api):
    spec = make_spec(api)
    offering = make_offering(api, spec, description="100 Mbit/s")
    assert offering["productSpecification"] == {
        "id": spec["id"],
        "href": spec["href"],
        "name": "Fiber",
        "@type": "ProductSpecificationRef",
        "@referredType": "ProductSpecification",
    }
    assert offering["isSellable"] is True
    assert offering["productOfferingPrice"] == []
    assert offering["validFor"] == {"startDateTime": z(timeline.START)}
    assert api.get(offering["href"]).json() == offering


def test_an_offering_specification_is_optional_but_must_exist(api):
    spec = make_spec(api)
    body = offering_body(spec)
    del body["productSpecification"]
    without = api.post(OFFERING, body)
    assert without.status_code == 201, without.text  # optional in the spec
    assert "productSpecification" not in without.json()
    unknown = api.post(OFFERING, offering_body({"id": NIL, "href": "/x", "name": "x"}))
    assert unknown.status_code == 422
    assert "does not exist" in unknown.json()["message"]
    assert (
        api.post(OFFERING, offering_body({"id": "nope", "href": "/x", "name": "x"})).status_code
        == 422
    )


def test_offering_filters(api):
    fiber, tv = make_spec(api, "Fiber"), make_spec(api, "TV")
    make_offering(api, fiber, name="Fiber 100")
    make_offering(api, fiber, name="Fiber 500", isSellable=False)
    make_offering(api, tv, name="TV basic")

    def names(query: str) -> list[str]:
        return [item["name"] for item in api.get(f"{OFFERING}?{query}").json()]

    assert names(f"productSpecification.id={fiber['id']}") == ["Fiber 100", "Fiber 500"]
    assert names("isSellable=false") == ["Fiber 500"]
    assert names(f"productSpecification.id={tv['id']}&isSellable=true") == ["TV basic"]
    assert api.get(f"{OFFERING}?productSpecification.id=nope").status_code == 400
    assert api.get(f"{OFFERING}?isSellable=yes").status_code == 400


def test_price_round_trip(api):
    price = make_price(api, price={"unit": "USD", "value": 499.9})
    assert decimals(api.get(price["href"]))["price"] == {"unit": "USD", "value": Decimal("499.90")}
    assert price["priceType"] == "recurring"
    assert price["recurringChargePeriodType"] == "monthly"
    assert price["recurringChargePeriodLength"] == 1
    one_time = make_price(api, priceType="oneTime", recurringChargePeriodType=None, name="Setup")
    assert "recurringChargePeriodType" not in one_time
    assert "recurringChargePeriodLength" not in one_time
    assert [p["name"] for p in api.get(f"{PRICE}?priceType=oneTime").json()] == ["Setup"]


def test_price_validation(api):
    def status(**over) -> int:
        return api.post(PRICE, price_body(**over)).status_code

    assert status(recurringChargePeriodType=None) == 422  # recurring needs a period
    assert status(priceType="oneTime") == 422  # and a oneTime price must not have one
    assert status(recurringChargePeriodType="fortnightly") == 422
    assert status(recurringChargePeriodLength=0) == 422
    assert status(priceType="usage") == 422
    assert status(price={"unit": "usd", "value": 1}) == 422
    assert status(price={"unit": "USD"}) == 422
    assert status(price={"unit": "USD", "value": 1, "taxRate": 20}) == 400
    assert api.get(PRICE).json() == []


def test_prices_attach_on_create_and_on_patch(api):
    monthly = make_price(api)
    setup = make_price(api, priceType="oneTime", recurringChargePeriodType=None, name="Setup")
    offering = make_offering(api, productOfferingPrice=price_refs(monthly))
    assert [p["id"] for p in offering["productOfferingPrice"]] == [monthly["id"]]
    assert offering["productOfferingPrice"][0] == {
        "id": monthly["id"],
        "href": monthly["href"],
        "name": "Monthly",
        "@type": "ProductOfferingPriceRef",
        "@referredType": "ProductOfferingPrice",
    }

    patched = api.patch(offering["href"], {"productOfferingPrice": price_refs(monthly, setup)})
    assert patched.status_code == 200
    assert [p["id"] for p in patched.json()["productOfferingPrice"]] == [monthly["id"], setup["id"]]
    # repeating the same list is a no-op
    again = api.patch(offering["href"], {"productOfferingPrice": price_refs(monthly, setup)})
    assert again.status_code == 200


def test_a_price_is_never_detached(api):
    monthly = make_price(api)
    setup = make_price(api, priceType="oneTime", recurringChargePeriodType=None)
    offering = make_offering(api, productOfferingPrice=price_refs(monthly, setup))
    for remaining in ([], price_refs(setup)):
        response = api.patch(offering["href"], {"productOfferingPrice": remaining})
        assert response.status_code == 409
        assert "cannot be detached" in response.json()["message"]
    assert api.patch(offering["href"], {"productOfferingPrice": None}).status_code == 422
    assert len(api.get(offering["href"]).json()["productOfferingPrice"]) == 2


def test_a_price_belongs_to_at_most_one_offering(api):
    price = make_price(api)
    make_offering(api, productOfferingPrice=price_refs(price))
    second = api.post(
        OFFERING, offering_body(make_spec(api), productOfferingPrice=price_refs(price))
    )
    assert second.status_code == 409
    assert "already belongs to another offering" in second.json()["message"]
    unknown = api.post(OFFERING, offering_body(make_spec(api), productOfferingPrice=[{"id": NIL}]))
    assert unknown.status_code == 422
    assert len(api.get(OFFERING).json()) == 1


def test_overlapping_prices_cannot_be_attached_to_one_offering(api):
    first, overlapping = make_price(api), make_price(api)
    other_currency = make_price(api, price={"unit": "EUR", "value": 5})
    offering = make_offering(api, productOfferingPrice=price_refs(first, other_currency))
    response = api.patch(
        offering["href"], {"productOfferingPrice": price_refs(first, other_currency, overlapping)}
    )
    assert response.status_code == 409
    assert "overlaps" in response.json()["message"]


def test_changing_a_price_means_ending_it_and_attaching_a_new_one(api):
    old = make_price(api)
    offering = make_offering(api, productOfferingPrice=price_refs(old))
    switch = iso(timeline.START + timedelta(days=31))
    # (the charge itself is fixed once the offering is ordered:
    # tests/catalog/test_api_catalog_attributes.py)
    moved = api.patch(old["href"], {"validFor": {"startDateTime": switch}})
    assert moved.status_code == 409

    ended = api.patch(old["href"], {"validFor": {"startDateTime": START, "endDateTime": switch}})
    assert ended.status_code == 200
    assert ended.json()["validFor"]["endDateTime"] == z(timeline.START + timedelta(days=31))
    new = make_price(api, price={"unit": "USD", "value": 600}, validFor={"startDateTime": switch})
    attached = api.patch(offering["href"], {"productOfferingPrice": price_refs(old, new)})
    assert attached.status_code == 200

    # reopening the old price would overlap the new one
    reopened = api.patch(old["href"], {"validFor": {"startDateTime": START}})
    assert reopened.status_code == 409
    assert decimals(api.get(old["href"]))["price"]["value"] == Decimal("500.00")


def test_offering_patch(api):
    offering = make_offering(api, description="old")
    patched = api.patch(
        offering["href"],
        {"isSellable": False, "lifecycleStatus": "Retired", "description": None},
    ).json()
    assert patched["isSellable"] is False and patched["lifecycleStatus"] == "Retired"
    assert "description" not in patched
    assert api.patch(offering["href"], {"productSpecification": {"id": NIL}}).status_code == 422
    assert api.patch(offering["href"], {"isSellable": None}).status_code == 422
    assert api.patch(offering["href"], {"validFor": {}}).status_code == 422
    assert api.patch(f"{OFFERING}/{NIL}", {"name": "x"}).status_code == 404


def test_a_price_cannot_be_ended_retroactively(api):
    """An end before now would change which price applied to periods that may already
    be billed. The price starts at START; the clock is then moved to a day after it."""
    price = make_price(api)
    now = timeline.START + timedelta(days=10)
    api.clock.set(now)

    def end_at(when):
        period = {"startDateTime": START} | ({"endDateTime": iso(when)} if when else {})
        return api.patch(price["href"], {"validFor": period})

    for past in (now - timedelta(days=5), now - timedelta(seconds=1)):
        response = end_at(past)
        assert response.status_code == 409, past
        assert "present or the future" in response.json()["message"]
    assert "endDateTime" not in api.get(price["href"]).json()["validFor"]

    assert end_at(now).status_code == 200  # exactly now
    assert end_at(now + timedelta(days=60)).status_code == 200  # moved later
    assert end_at(now - timedelta(hours=1)).status_code == 409  # moved back into the past
    assert end_at(None).status_code == 200  # reopened: the end was still in the future

    ended_at = now + timedelta(days=365)
    api.clock.set(ended_at - timedelta(days=1))
    assert end_at(ended_at).status_code == 200
    api.clock.set(ended_at + timedelta(days=365))
    # the price ended a year ago: neither extending nor reopening it is allowed any more
    assert end_at(ended_at + timedelta(days=400)).status_code == 409
    assert end_at(None).status_code == 409
    # patching something else, or repeating the same period, is still fine
    assert api.patch(price["href"], {"name": "Old monthly"}).status_code == 200
    assert end_at(ended_at).status_code == 200


def test_a_price_cannot_start_in_the_past(api):
    """Stage 3 catch-up billing charges past days at the price valid then; a price that
    starts in the past would add back-charges. Neither creating nor attaching one works."""
    past, now = iso(NOW - timedelta(seconds=1)), iso(NOW)
    response = api.post(PRICE, price_body(validFor={"startDateTime": past}))
    assert response.status_code == 409
    assert "in the past" in response.json()["message"]
    assert api.get(PRICE).json() == []
    assert api.post(PRICE, price_body(validFor={"startDateTime": now})).status_code == 201

    # A price created for the future becomes a past-starting one once time passes; from
    # then on it can no longer be attached - not on create and not on patch.
    later = make_price(api, priceType="oneTime", recurringChargePeriodType=None)
    api.clock.set(timeline.START + timedelta(seconds=1))
    on_create = api.post(
        OFFERING, offering_body(make_spec(api), productOfferingPrice=price_refs(later))
    )
    assert on_create.status_code == 409
    assert "in the past" in on_create.json()["message"]
    offering = make_offering(api)
    on_patch = api.patch(offering["href"], {"productOfferingPrice": price_refs(later)})
    assert on_patch.status_code == 409
    assert api.get(offering["href"]).json()["productOfferingPrice"] == []
