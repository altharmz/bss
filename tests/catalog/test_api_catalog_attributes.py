"""TMF620 attributes (docs/decisions/catalog.md, "Catalog attributes"): every attribute
the spec defines for specifications, offerings, prices and catalogs is taken, stored and
returned. The descriptive ones are documents, checked against the spec at every depth;
what has behaviour - an offering's specification, categories, code and inline prices, a
price's charge - follows the catalog's rules."""

import copy
from typing import Any

import pytest

from app.api import catalog, classification, specschema
from tests import apidata, contract, timeline
from tests.apidata import CATALOG
from tests.builders.catalog.api_catalog import (
    NIL,
    OFFERING,
    PRICE,
    SPEC,
    START,
    make_offering,
    make_price,
    make_spec,
    offering_body,
    price_body,
    price_refs,
)
from tests.builders.tmf.events import stored, subscribe
from tests.timeline import NOW, z

CATEGORY = f"{CATALOG}/category"
PRODUCT_CATALOG = f"{CATALOG}/productCatalog"
SERVER = ("id", "href", "lastUpdate")


def example(name: str) -> dict[str, Any]:
    """A TMF620 request example, made valid against the spec's own schemas. The
    examples disagree with them in a few places, and the schemas win (as everywhere):
    an AttachmentRef has no mimeType, a number characteristic's value is a number (not
    "8"), a ProductSpecificationRelationship no role, a ProductOfferingPriceRelationship
    no validFor, a PricingLogicAlgorithm no href."""
    examples = contract.spec("TMF620")["components"]["examples"]
    return _valid(copy.deepcopy(examples[name]["value"]))


def _valid(value: Any, attribute: str = "") -> Any:
    if isinstance(value, list):
        return [_valid(item, attribute) for item in value]
    if not isinstance(value, dict):
        return value
    drop = {
        "attachment": {"mimeType"} if value.get("@type") == "AttachmentRef" else set(),
        "productSpecificationRelationship": {"role"},
        "popRelationship": {"validFor"},
        "pricingLogicAlgorithm": {"href"},
    }.get(attribute, set())
    out = {k: _valid(v, k) for k, v in value.items() if k not in drop}
    if out.get("valueType") == "number" and isinstance(out.get("value"), str):
        out["value"] = int(out["value"])
    return out


def without(body: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in names}


def test_every_spec_attribute_has_a_place():
    """Documents plus the attributes with behaviour are exactly the spec's attributes,
    in both the create (_FVO) and the patch (_MVO) schema."""
    common = {"name", "description", "lifecycleStatus", "lastUpdate"}
    for schema, documents, taken in (
        ("ProductSpecification", catalog.SPEC_DOCUMENTS, common),
        (
            "ProductOffering",
            catalog.OFFERING_DOCUMENTS,
            common
            | {"isSellable", "validFor", "productSpecification", "productOfferingPrice"}
            | {"category"},
        ),
        (
            "ProductOfferingPrice",
            catalog.PRICE_DOCUMENTS,
            common - {"description"}
            | {"priceType", "price", "validFor"}
            | {"recurringChargePeriodType", "recurringChargePeriodLength"},
        ),
        (
            "ProductCatalog",
            classification.CATALOG_DOCUMENTS,
            set(classification.CATALOG_ATTRIBUTES) - set(classification.CATALOG_DOCUMENTS),
        ),
    ):
        assert len(set(documents)) == len(documents)
        for suffix in ("_FVO", "_MVO"):
            names = set(specschema.properties("TMF620", schema + suffix))
            names -= {"id", "href", "@type", "@baseType", "@schemaLocation"}
            expected = taken | set(documents)
            if suffix == "_MVO":  # server-managed: most _MVO schemas leave it out
                names, expected = names - {"lastUpdate"}, expected - {"lastUpdate"}
            assert names == expected, (schema, suffix, names ^ expected)


def test_the_spec_s_specification_example_round_trips(api):
    """TMF620's own Product_Specification_Create_example: its references (bundled and
    related specifications, attachments, parties) point to entries we do not have and
    are stored as sent."""
    body = without(example("Product_Specification_Create_example_request"), "id", "href")
    response = api.post(SPEC, body)
    assert response.status_code == 201, response.text
    spec = response.json()
    expected = copy.deepcopy(without(body, "lastUpdate"))
    relationship = expected["productSpecCharacteristic"][0]["charSpecRelationship"][0]
    assert relationship["validFor"] == {"startDateTime": "2020-09-23T16:42:23-04:00"}
    relationship["validFor"] = {"startDateTime": "2020-09-23T20:42:23Z"}  # back in UTC
    assert without(spec, *SERVER) == expected
    assert api.get(spec["href"]).json() == spec
    assert api.get(f"{spec['href']}?fields=brand").json() == {
        "id": spec["id"],
        "href": spec["href"],
        "@type": "ProductSpecification",
        "brand": "Cisco",
    }


def test_the_spec_s_price_example_round_trips(api):
    """Product_Offering_Price_Create_example, starting at START (a price never starts in
    the past): its tax, terms, percentage and popRelationship are records."""
    body = without(example("Product_Offering_Price_Create_example_request"), "id", "href")
    body["validFor"] = {"startDateTime": z(timeline.START)}
    response = api.post(PRICE, body)
    assert response.status_code == 201, response.text
    price = response.json()
    assert without(price, *SERVER) == without(body, "lastUpdate") | {
        "price": {"unit": "EUR", "value": 50.0},
    }
    assert api.get(price["href"]).json() == price


def test_the_spec_s_offering_example_round_trips(api):
    """Product_Offering_Create_example against entries of ours: the specification at
    version 1.1 and the category at 2.0 that it refers to, validFor from START. The
    example types its price reference `ProductOfferingPrice`, which its own
    discriminator maps to an inline price: it is sent as a ProductOfferingPriceRef."""
    spec = make_spec(api)
    assert api.patch(spec["href"], {"version": "1.1"}).status_code == 200
    category = api.post(CATEGORY, {"name": "Cloud", "version": "2.0"}).json()
    price = make_price(api)
    body = without(example("Product_Offering_Create_example_request"), "id", "href")
    body["validFor"] = {"startDateTime": z(timeline.START)}
    body["productSpecification"] |= {"id": spec["id"], "href": spec["href"]}
    body["category"][0] |= {"id": category["id"], "href": category["href"]}
    body["productOfferingPrice"] = [
        {"id": price["id"], "@type": "ProductOfferingPriceRef", "name": price["name"]}
    ]
    response = api.post(OFFERING, body)
    assert response.status_code == 201, response.text
    offering = response.json()
    expected = without(
        body, "lastUpdate", "productSpecification", "category", "productOfferingPrice"
    )
    assert without(
        offering, *SERVER, "productSpecification", "category", "productOfferingPrice"
    ) == (expected)
    assert offering["productSpecification"]["version"] == "1.1"
    assert offering["category"] == [
        {
            "id": category["id"],
            "href": category["href"],
            "name": "Cloud",
            "@type": "CategoryRef",
            "@referredType": "Category",
            "version": "2.0",
        }
    ]
    assert [p["id"] for p in offering["productOfferingPrice"]] == [price["id"]]
    assert api.get(category["href"]).json()["productOffering"][0]["id"] == offering["id"]
    assert api.get(offering["href"]).json() == offering


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"attachment": [{"@type": "AttachmentRef", "id": "1", "colour": 1}]}, 400,
         "attachment.0.colour: unknown attribute"),
        ({"productOfferingTerm": [{"@type": "ProductOfferingTerm"}]}, 422,
         "productOfferingTerm.0.name: Field required"),
        ({"isBundle": "yes"}, 422, "isBundle: must be a boolean"),
        ({"place": [{"id": "1"}]}, 422, "place.0.@type: Field required"),
    ],
)  # fmt: skip
def test_documents_are_checked_against_the_spec(api, body, status, message):
    response = api.post(OFFERING, offering_body(make_spec(api), **body))
    assert response.status_code == status, response.text
    assert message in response.json()["message"]
    assert api.get(OFFERING).json() == []


def test_documents_are_replaced_by_a_merge_patch_and_removed_by_null(api):
    spec = make_spec(api)
    term = {"@type": "ProductOfferingTerm", "name": "12 months"}
    offering = make_offering(api, spec, version="1", productOfferingTerm=[term])
    patched = api.patch(offering["href"], {"version": "2", "statusReason": "launched"})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body["version"], body["statusReason"], body["productOfferingTerm"]) == (
        "2",
        "launched",
        [term],
    )
    removed = api.patch(offering["href"], {"productOfferingTerm": None}).json()
    assert "productOfferingTerm" not in removed and removed["version"] == "2"
    json_patch = api.patch(
        spec["href"],
        [{"op": "add", "path": "/brand", "value": "Acme"}],
        content_type="application/json-patch+json",
    )
    assert json_patch.status_code == 200, json_patch.text
    assert json_patch.json()["brand"] == "Acme"


def test_a_reference_names_the_current_version_or_none(api):
    spec = make_spec(api)
    assert api.patch(spec["href"], {"version": "1.1"}).status_code == 200
    ref = {"id": spec["id"], "@type": "ProductSpecificationRef"}
    stale = api.post(OFFERING, offering_body(spec, productSpecification=ref | {"version": "1.0"}))
    assert stale.status_code == 422
    assert stale.json()["message"] == (
        f"productSpecification.version: {spec['id']} is referred to at version 1.0, but its"
        " current version is 1.1"
    )
    schema = {"@type": "Firewall", "@schemaLocation": "https://example.org/f.json"}
    wrong = api.post(
        OFFERING, offering_body(spec, productSpecification=ref | {"targetProductSchema": schema})
    )
    assert wrong.status_code == 422
    assert "targetProductSchema" in wrong.json()["message"]
    assert (
        api.post(
            OFFERING, offering_body(spec, productSpecification=ref | {"version": "1.1"})
        ).status_code
        == 201
    )

    root = api.post(CATEGORY, {"name": "Root"}).json()  # no version
    child = api.post(CATEGORY, {"name": "Child", "parent": {"id": root["id"], "version": "1"}})
    assert child.status_code == 422
    assert "it has no version" in child.json()["message"]
    listed = api.post(
        PRODUCT_CATALOG, {"name": "Main", "category": [{"id": root["id"], "version": "2"}]}
    )
    assert listed.status_code == 422


def test_inline_prices_are_created_under_price_post_s_rules(api):
    spec = make_spec(api)
    inline = without(price_body(), "validFor") | {"name": "Inline monthly"}
    offering = make_offering(api, spec, productOfferingPrice=[inline])
    (attached,) = offering["productOfferingPrice"]
    price = api.get(attached["href"]).json()
    assert price["name"] == "Inline monthly" and price["validFor"] == {"startDateTime": z(NOW)}

    # an entry with id and no @type is a reference; one without either is a new price
    more = without(price_body(), "validFor", "recurringChargePeriodType") | {
        "name": "One-time",
        "priceType": "oneTime",
    }
    patched = api.patch(offering["href"], {"productOfferingPrice": [{"id": price["id"]}, more]})
    assert patched.status_code == 200, patched.text
    assert len(patched.json()["productOfferingPrice"]) == 2

    past = price_body(validFor={"startDateTime": z(NOW.replace(year=2025))})
    refused = api.post(OFFERING, offering_body(spec, name="Past", productOfferingPrice=[past]))
    assert refused.status_code == 409  # a price never starts in the past
    assert len(api.get(PRICE).json()) == 2 and len(api.get(OFFERING).json()) == 1

    no_amount = without(price_body(), "price")
    missing = api.post(OFFERING, offering_body(spec, productOfferingPrice=[no_amount]))
    assert missing.status_code == 422
    assert "productOfferingPrice.0.price: required" in missing.json()["message"]
    unknown = api.post(OFFERING, offering_body(spec, productOfferingPrice=[price_body(colour=1)]))
    assert unknown.status_code == 400
    assert "productOfferingPrice.0.colour: unknown attribute" in unknown.json()["message"]


def test_inline_prices_emit_their_own_create_event(api, db_conn):
    subscribe(api, "TMF620")
    make_offering(api, productOfferingPrice=[price_body()])
    types = [t for t, _ in stored(db_conn, "TMF620")]
    assert types[-2:] == ["ProductOfferingPriceCreateEvent", "ProductOfferingCreateEvent"]


def test_a_price_may_have_no_amount_but_then_prices_no_offering(api):
    created = api.post(PRICE, without(price_body(), "price", "validFor") | {"percentage": 10})
    assert created.status_code == 201, created.text
    price = created.json()
    assert "price" not in price and price["percentage"] == 10
    assert price["validFor"] == {"startDateTime": z(NOW)}  # absent: from now on
    refused = api.post(
        OFFERING, offering_body(make_spec(api), productOfferingPrice=price_refs(price))
    )
    assert refused.status_code == 409
    assert "has no price" in refused.json()["message"]
    # once attached, a price keeps an amount
    attached = make_price(api)
    make_offering(api, productOfferingPrice=price_refs(attached), name="Other")
    removed = api.patch(attached["href"], {"price": None})
    assert removed.status_code == 409


def test_a_price_s_charge_changes_while_it_has_priced_nothing(api):
    loose = make_price(api)
    changed = api.patch(
        loose["href"],
        {
            "price": {"unit": "EUR", "value": 7},
            "priceType": "oneTime",
            "recurringChargePeriodType": None,
        },
    )
    assert changed.status_code == 200, changed.text
    assert (
        changed.json()["priceType"] == "oneTime"
        and "recurringChargePeriodType" not in changed.json()
    )
    inconsistent = api.patch(loose["href"], {"priceType": "recurring"})
    assert inconsistent.status_code == 422  # a recurring price needs its period

    price = make_price(api)
    offering = make_offering(api, productOfferingPrice=price_refs(price))
    assert api.patch(price["href"], {"price": {"unit": "USD", "value": 550}}).status_code == 200

    apidata.order(api, apidata.account(api), apidata.add_item(offering))  # ordered: fixed now
    for change in (
        {"price": {"unit": "USD", "value": 600}},
        {"price": {"unit": "EUR", "value": 550}},
        {"priceType": "oneTime", "recurringChargePeriodType": None},
    ):
        response = api.patch(price["href"], change)
        assert response.status_code == 409, change
        assert "end its validFor and attach a new price" in response.json()["message"]
    # repeating the current charge is no change
    same = {
        "price": {"unit": "USD", "value": 550},
        "priceType": "recurring",
        "recurringChargePeriodType": "monthly",
        "recurringChargePeriodLength": 1,
    }
    assert api.patch(price["href"], same).status_code == 200


def test_an_offering_s_specification_changes_until_it_is_ordered(api):
    first, second = make_spec(api, "First"), make_spec(api, "Second")
    offering = make_offering(api, first, productOfferingPrice=price_refs(make_price(api)))
    moved = api.patch(offering["href"], {"productSpecification": {"id": second["id"]}})
    assert moved.status_code == 200, moved.text
    assert moved.json()["productSpecification"]["id"] == second["id"]
    removed = api.patch(offering["href"], {"productSpecification": None})
    assert removed.status_code == 200 and "productSpecification" not in removed.json()
    assert (
        api.patch(offering["href"], {"productSpecification": {"id": first["id"]}}).status_code
        == 200
    )

    apidata.order(api, apidata.account(api), apidata.add_item(offering))
    refused = api.patch(offering["href"], {"productSpecification": {"id": second["id"]}})
    assert refused.status_code == 409
    assert "the offering was ordered" in refused.json()["message"]
    same = api.patch(offering["href"], {"productSpecification": {"id": first["id"]}})
    assert same.status_code == 200


def test_an_offering_without_validity_or_specification_is_ordered_any_time(api):
    price = make_price(api)
    body = without(offering_body(make_spec(api)), "validFor", "productSpecification")
    created = api.post(OFFERING, body | {"productOfferingPrice": price_refs(price)})
    assert created.status_code == 201, created.text
    offering = created.json()
    assert "validFor" not in offering and "productSpecification" not in offering
    account = apidata.account(api)
    placed = apidata.order(api, account, apidata.add_item(offering), requestedStartDate=START)
    assert placed["state"] == "completed", placed
    product = apidata.product_of(api, placed)
    assert "productSpecification" not in product
    products = api.get(f"/internal/v1/billingAccount/{account['id']}/products").json()
    assert products[0]["productSpecification"] is None
    # setting a validFor that starts in the past would rewrite what was orderable then
    assert (
        api.patch(
            offering["href"], {"validFor": {"startDateTime": z(NOW.replace(year=2025))}}
        ).status_code
        == 409
    )


def test_an_offering_s_categories_are_written_from_its_side_too(api):
    a = api.post(CATEGORY, {"name": "A"}).json()
    b = api.post(CATEGORY, {"name": "B"}).json()
    offering = make_offering(api, category=[{"id": a["id"]}])
    assert [c["id"] for c in offering["category"]] == [a["id"]]
    patched = api.patch(offering["href"], {"category": [{"id": b["id"]}, {"id": a["id"]}]})
    assert patched.status_code == 200, patched.text
    assert {c["id"] for c in patched.json()["category"]} == {a["id"], b["id"]}
    assert [o["id"] for o in api.get(b["href"]).json()["productOffering"]] == [offering["id"]]
    duplicate = api.patch(offering["href"], {"category": [{"id": a["id"]}, {"id": a["id"]}]})
    assert duplicate.status_code == 422
    unknown = api.patch(offering["href"], {"category": [{"id": NIL}]})
    assert unknown.status_code == 422
    assert api.patch(offering["href"], {"category": None}).status_code == 422
    # a category with offerings is still not deleted
    assert api.delete(b["href"]).status_code == 409


def test_a_catalog_s_related_party_is_stored_as_sent(api):
    party = [
        {
            "@type": "RelatedPartyRefOrPartyRoleRef",
            "role": "Owner",
            "partyOrPartyRole": {"@type": "PartyRef", "id": "1234", "name": "Somebody"},
        }
    ]
    created = api.post(PRODUCT_CATALOG, {"name": "Main", "relatedParty": party})
    assert created.status_code == 201, created.text
    assert created.json()["relatedParty"] == party
    removed = api.patch(created.json()["href"], {"relatedParty": None})
    assert removed.status_code == 200 and "relatedParty" not in removed.json()
    bad = api.post(PRODUCT_CATALOG, {"name": "X", "relatedParty": [{"role": "Owner"}]})
    assert bad.status_code == 422
