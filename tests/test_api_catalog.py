"""TMF620 Product Catalog: specifications, offerings, prices and how prices attach."""

from decimal import Decimal

from tests.apiclient import decimals

BASE = "/tmf-api/productCatalogManagement/v5"
SPEC = f"{BASE}/productSpecification"
OFFERING = f"{BASE}/productOffering"
PRICE = f"{BASE}/productOfferingPrice"
START = "2026-01-01T00:00:00+03:00"
NIL = "00000000-0000-0000-0000-000000000000"


def make_spec(api, name="Fiber") -> dict:
    body = {"@type": "ProductSpecification", "name": name, "lifecycleStatus": "Active"}
    response = api.post(SPEC, body)
    assert response.status_code == 201, response.text
    return response.json()


def price_body(**over) -> dict:
    return {
        "@type": "ProductOfferingPrice",
        "name": "Monthly",
        "lifecycleStatus": "Active",
        "priceType": "recurring",
        "recurringChargePeriodType": "monthly",
        "price": {"unit": "RUB", "value": 500},
        "validFor": {"startDateTime": START},
        **over,
    }


def make_price(api, **over) -> dict:
    response = api.post(PRICE, price_body(**over))
    assert response.status_code == 201, response.text
    return response.json()


def offering_body(spec: dict, **over) -> dict:
    return {
        "@type": "ProductOffering",
        "name": "Fiber 100",
        "lifecycleStatus": "Active",
        "validFor": {"startDateTime": START},
        "productSpecification": {
            "id": spec["id"],
            "href": spec["href"],
            "name": spec["name"],
            "@type": "ProductSpecificationRef",
            "@referredType": "ProductSpecification",
        },
        **over,
    }


def make_offering(api, spec=None, **over) -> dict:
    response = api.post(OFFERING, offering_body(spec or make_spec(api), **over))
    assert response.status_code == 201, response.text
    return response.json()


def price_refs(*prices: dict) -> list[dict]:
    return [{"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in prices]


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
    assert offering["validFor"] == {"startDateTime": "2025-12-31T21:00:00Z"}
    assert api.get(offering["href"]).json() == offering


def test_offering_needs_an_existing_specification(api):
    spec = make_spec(api)
    body = offering_body(spec)
    del body["productSpecification"]
    assert api.post(OFFERING, body).status_code == 422
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
    price = make_price(api, price={"unit": "RUB", "value": 499.9})
    assert decimals(api.get(price["href"]))["price"] == {"unit": "RUB", "value": Decimal("499.90")}
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
    assert status(recurringChargePeriodType="weekly") == 422
    assert status(recurringChargePeriodLength=3) == 422
    assert status(priceType="usage") == 422
    assert status(price={"unit": "rub", "value": 1}) == 422
    assert status(price={"unit": "RUB"}) == 422
    assert status(price={"unit": "RUB", "value": 1, "taxRate": 20}) == 400
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
    switch = "2026-11-01T00:00:00+03:00"  # after the fixture clock (2026-10-01T09:00Z)

    for attribute, value in (
        ("price", {"unit": "RUB", "value": 600}),
        ("priceType", "oneTime"),
        ("recurringChargePeriodType", "monthly"),
        ("recurringChargePeriodLength", 1),
    ):
        response = api.patch(old["href"], {attribute: value})
        assert response.status_code == 400, attribute
        assert f"{attribute} is read-only" in response.json()["message"]
    moved = api.patch(old["href"], {"validFor": {"startDateTime": switch}})
    assert moved.status_code == 409

    ended = api.patch(old["href"], {"validFor": {"startDateTime": START, "endDateTime": switch}})
    assert ended.status_code == 200
    assert ended.json()["validFor"]["endDateTime"] == "2026-10-31T21:00:00Z"
    new = make_price(api, price={"unit": "RUB", "value": 600}, validFor={"startDateTime": switch})
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
    assert api.patch(offering["href"], {"productSpecification": {"id": NIL}}).status_code == 400
    assert api.patch(offering["href"], {"isSellable": None}).status_code == 422
    assert api.patch(offering["href"], {"validFor": {}}).status_code == 422
    assert api.patch(f"{OFFERING}/{NIL}", {"name": "x"}).status_code == 404


def test_a_price_cannot_be_ended_retroactively(api):
    """Clock: 2026-10-01T09:00Z. An end before that would change which price applied to
    periods that may already be billed."""
    price = make_price(api)

    def end_at(when: str | None):
        period = {"startDateTime": START} | ({"endDateTime": when} if when else {})
        return api.patch(price["href"], {"validFor": period})

    for past in ("2026-06-01T00:00:00Z", "2026-10-01T08:59:59Z"):
        response = end_at(past)
        assert response.status_code == 409, past
        assert "present or the future" in response.json()["message"]
    assert "endDateTime" not in api.get(price["href"]).json()["validFor"]

    assert end_at("2026-10-01T09:00:00Z").status_code == 200  # exactly now
    assert end_at("2026-12-01T00:00:00Z").status_code == 200  # moved later
    assert end_at("2026-10-01T08:00:00Z").status_code == 409  # moved back into the past
    assert end_at(None).status_code == 200  # reopened: the end was still in the future

    api.clock.set(api.clock.now().replace(year=2027))
    ended = end_at("2027-10-01T09:00:00Z")
    assert ended.status_code == 200
    api.clock.set(api.clock.now().replace(year=2028))
    # the price ended a year ago: neither extending nor reopening it is allowed any more
    assert end_at("2029-01-01T00:00:00Z").status_code == 409
    assert end_at(None).status_code == 409
    # patching something else, or repeating the same period, is still fine
    assert api.patch(price["href"], {"name": "Old monthly"}).status_code == 200
    same = {"startDateTime": START, "endDateTime": "2027-10-01T09:00:00Z"}
    assert api.patch(price["href"], {"validFor": same}).status_code == 200
