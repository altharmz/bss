"""TMF620 refusals the other catalog tests leave out (#484): price and offering-price
rules on PATCH, malformed productOfferingPrice entries, an offering kind that its
multi-line discount forbids, and the writes that lose a race to another transaction.
Each asserts the TMF error and that nothing was stored."""

from uuid import uuid4

import pytest

from app.repository import catalog
from tests.builders.catalog.api_catalog import (
    OFFERING,
    PRICE,
    make_offering,
    make_price,
    make_spec,
    offering_body,
    price_body,
    price_refs,
)
from tests.builders.prepaid.prepaid_plans import kind_characteristic

LINES = {"units": "lines"}


def zone_use(*values):
    return [
        {
            "@type": "ProductSpecCharacteristicValueUse",
            "name": "usageZone",
            "valueType": "string",
            "productSpecCharacteristicValue": [
                {
                    "@type": "StringCharacteristicValueSpecification",
                    "valueType": "string",
                    "value": v,
                }
                for v in values
            ],
        }
    ]


def refused(response, status, message):
    assert response.status_code == status, response.text
    assert message in response.json()["message"], response.text


# --- price rules ---


def test_a_tax_amount_needs_the_price_it_is_the_tax_of(api):
    body = {k: v for k, v in price_body().items() if k != "price"} | {"percentage": 10}
    tax = {"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}
    body["tax"] = [tax | {"taxAmount": {"unit": "USD", "value": 1}}]
    response = api.post(PRICE, body)
    refused(response, 422, "tax.0.taxAmount: needs the price it is the tax of")
    assert api.get(PRICE).json() == []


def test_a_period_length_is_only_for_a_recurring_price(api):
    setup = make_price(api, priceType="oneTime", recurringChargePeriodType=None)
    response = api.patch(setup["href"], {"recurringChargePeriodLength": 2})
    refused(response, 422, "recurringChargePeriodLength is only for a recurring price")
    assert api.get(setup["href"]).json() == setup


def test_a_plain_discount_never_becomes_a_multi_line_tier(api):
    body = price_body(priceType="discount")
    del body["recurringChargePeriodType"]  # a discount without a unit has no period
    response = api.post(PRICE, body)
    assert response.status_code == 201, response.text
    plain = response.json()
    for unit, message in [
        ({**LINES, "amount": 1}, "a whole number of 2 or more"),
        ({**LINES, "amount": 3}, "a multi-line discount is created as one"),
    ]:
        refused(api.patch(plain["href"], {"unitOfMeasure": unit}), 422, message)
    assert api.get(plain["href"]).json() == plain
    # another unit keeps it a plain discount
    other = api.patch(plain["href"], {"unitOfMeasure": {"amount": 2, "units": "GB"}})
    assert other.status_code == 200, other.text
    assert api.get(plain["href"]).json()["unitOfMeasure"] == {"amount": 2, "units": "GB"}


def test_a_usage_zone_is_one_known_zone_on_a_usage_price(api):
    monthly = make_price(api)
    for use, message in [
        (zone_use("mars"), "prodSpecCharValueUse usageZone: one value of domestic"),
        (zone_use("roaming", "domestic"), "prodSpecCharValueUse usageZone: one value of"),
        (zone_use("roaming"), "usageZone is only for a usage price"),
    ]:
        refused(api.patch(monthly["href"], {"prodSpecCharValueUse": use}), 422, message)
    assert api.get(monthly["href"]).json() == monthly


# --- productOfferingPrice entries of an offering ---


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("monthly", "productOfferingPrice.0: must be an object"),
        (
            {"@type": "Price", "name": "x"},
            "productOfferingPrice.0.@type: must be one of: ProductOfferingPrice,"
            " ProductOfferingPriceRef",
        ),
        ({**price_body(), "id": "P-1"}, "productOfferingPrice.0.id: must be a UUID"),
    ],
)
def test_malformed_price_entries_are_refused(api, entry, message):
    body = offering_body(make_spec(api), productOfferingPrice=[entry])
    response = api.post(OFFERING, body)
    assert response.status_code in (400, 422), response.text
    assert message in response.json()["message"], response.text
    assert api.get(OFFERING).json() == [] and api.get(PRICE).json() == []


def test_an_offering_with_a_multi_line_discount_stays_a_plan(api):
    tier = make_price(
        api, priceType="discount", name="2+ lines", unitOfMeasure={**LINES, "amount": 2}
    )
    plan = make_offering(api, productOfferingPrice=price_refs(tier))
    addon = {"productOfferingCharacteristic": [kind_characteristic("addOn")]}
    refused(api.patch(plan["href"], addon), 422, "a addOn offering has no multi-line discount")
    assert api.get(plan["href"]).json() == plan


# --- writes that lose a race: another transaction commits first ---


@pytest.fixture
def rival(db_conn):
    """Another session; it fails rather than waits on a lock the request holds."""
    db_conn.execute("SET lock_timeout = '5s'")
    db_conn.commit()
    return db_conn


def race(monkeypatch, name, competing):
    """Run `competing` (committed in another session) right before the request's
    `catalog.<name>` call, as a concurrent transaction would."""
    real = getattr(catalog, name)

    def raced(conn, *args, **kwargs):
        competing()
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(catalog, name, raced)


def commit(conn, sql, params):
    conn.execute(sql, params)
    conn.commit()


def test_a_price_attached_elsewhere_meanwhile_is_a_conflict(api, rival, monkeypatch):
    price = make_price(api)
    other = make_offering(api, name="Other")
    attach = "UPDATE product_offering_price SET product_offering_id = %s WHERE id = %s"
    race(monkeypatch, "attach_price", lambda: commit(rival, attach, (other["id"], price["id"])))
    response = api.post(
        OFFERING, offering_body(make_spec(api), productOfferingPrice=price_refs(price))
    )
    refused(
        response, 409, f"productOfferingPrice {price['id']} already belongs to another offering"
    )
    assert [o["id"] for o in api.get(OFFERING).json()] == [other["id"]]
    assert price["id"] in [p["id"] for p in api.get(other["href"]).json()["productOfferingPrice"]]


def test_a_price_that_lost_its_amount_meanwhile_is_a_conflict(api, rival, monkeypatch):
    price = make_price(api)
    clear = "UPDATE product_offering_price SET amount = NULL WHERE id = %s"
    race(monkeypatch, "attach_price", lambda: commit(rival, clear, (price["id"],)))
    response = api.post(
        OFFERING, offering_body(make_spec(api), productOfferingPrice=price_refs(price))
    )
    refused(response, 409, f"productOfferingPrice {price['id']} has no price")
    assert api.get(OFFERING).json() == []


def test_an_offering_id_taken_meanwhile_is_a_conflict(api, rival, monkeypatch):
    uid, create = uuid4(), catalog.create_product_offering  # the real one, before the race

    def taken():
        create(
            rival,
            product_specification_id=None,
            name="Imported",
            lifecycle_status="Active",
            valid_for=None,
            id=uid,
        )
        rival.commit()

    race(monkeypatch, "create_product_offering", taken)
    response = api.post(OFFERING, offering_body(make_spec(api), id=str(uid)))
    refused(response, 409, f"productOffering {uid} already exists")
    assert [(o["id"], o["name"]) for o in api.get(OFFERING).json()] == [(str(uid), "Imported")]


def test_deleting_a_price_attached_meanwhile_is_a_conflict(api, rival, monkeypatch):
    price = make_price(api)
    offering = make_offering(api, name="Other")
    attach = "UPDATE product_offering_price SET product_offering_id = %s WHERE id = %s"
    competing = lambda: commit(rival, attach, (offering["id"], price["id"]))  # noqa: E731
    race(monkeypatch, "delete_unattached_price", competing)
    response = api.delete(price["href"])
    refused(response, 409, f"productOfferingPrice {price['id']} was attached meanwhile")
    assert api.get(price["href"]).status_code == 200
    attached = api.get(offering["href"]).json()["productOfferingPrice"]
    assert [p["id"] for p in attached] == [price["id"]]
