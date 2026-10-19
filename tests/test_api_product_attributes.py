"""TMF637 product attributes (docs/decisions/tmf-api.md#the-last-gaps-2026-10-18): the
product's own name and its descriptive attributes are stored as sent and change
directly; POST terminationDate, PATCH productOffering and PATCH status place orders;
billingAccount and startDate only repeat their value."""

from datetime import date

import pytest

from tests import apidata
from tests.test_api_product_writes import orders_of, product_body, shop, timeline_z
from tests.test_billing_db import at
from tests.timeline import NOW, iso, midnight

__all__ = ["shop"]  # the fixture, imported for these tests

PRODUCT = apidata.PRODUCT
OCT20, NOV1, NOV11 = date(2026, 10, 20), date(2026, 11, 1), date(2026, 11, 11)

CHARACTERISTIC = {"@type": "StringCharacteristic", "name": "speed", "value": "100 Mbit/s"}
DOCUMENTS = {
    "description": "Home internet",
    "isBundle": False,
    "isCustomerVisible": True,
    "productSerialNumber": "SN-1",
    "productCharacteristic": [CHARACTERISTIC],
    "productTerm": [{"@type": "ProductTerm", "name": "12 months commitment"}],
}


def test_post_stores_the_name_and_the_documents(api, shop):
    offering, account = shop
    response = api.post(PRODUCT, product_body(account, offering, name="Home", **DOCUMENTS))
    assert response.status_code == 201, response.text
    product = response.json()
    assert product["name"] == "Home"
    for name, value in DOCUMENTS.items():
        assert product[name] == value, name
    assert api.get(product["href"]).json() == product


def test_the_name_defaults_to_the_offering_s_at_creation(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    assert product["name"] == offering["name"]
    api.patch(f"{apidata.CATALOG}/productOffering/{offering['id']}", {"name": "Renamed"})
    assert api.get(product["href"]).json()["name"] == offering["name"]


def test_documents_are_checked_against_the_spec(api, shop):
    offering, account = shop
    unknown = {"productCharacteristic": [{**CHARACTERISTIC, "zzUnknown": 1}]}
    assert api.post(PRODUCT, product_body(account, offering, **unknown)).status_code == 400
    wrong = {"isBundle": "yes"}
    assert api.post(PRODUCT, product_body(account, offering, **wrong)).status_code == 422
    assert orders_of(api, account) == []


def test_patch_changes_the_name_and_documents_directly(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering, **DOCUMENTS)).json()
    response = api.patch(
        product["href"], {"name": "Renamed", "description": None, "productSerialNumber": "SN-2"}
    )
    assert response.status_code == 200, response.text
    patched = response.json()
    assert patched["name"] == "Renamed" and patched["productSerialNumber"] == "SN-2"
    assert "description" not in patched  # null removes
    assert patched["productCharacteristic"] == [CHARACTERISTIC]
    assert len(orders_of(api, account)) == 1  # no order: nothing created or ended
    assert api.patch(product["href"], {"name": None}).status_code == 422


def test_post_with_a_termination_date_places_two_orders(api, shop):
    offering, account = shop
    body = product_body(account, offering, terminationDate=iso(midnight(NOV11)))
    product = api.post(PRODUCT, body).json()
    assert product["status"] == "pendingTerminate"
    assert product["terminationDate"] == timeline_z(NOV11)
    actions = sorted(o["productOrderItem"][0]["action"] for o in orders_of(api, account))
    assert actions == ["add", "delete"]
    same_day = product_body(account, offering, terminationDate=iso(midnight(OCT20)))
    assert api.post(PRODUCT, same_day).status_code == 422


def test_a_reference_version_must_be_the_offering_s(api, shop):
    offering, account = shop
    api.patch(f"{apidata.CATALOG}/productOffering/{offering['id']}", {"version": "2"})
    old = {"id": offering["id"], "version": "1"}
    stale = api.post(PRODUCT, {**product_body(account, offering), "productOffering": old})
    assert stale.status_code == 422 and "version" in stale.json()["message"]
    current = {"id": offering["id"], "version": "2"}
    product = api.post(PRODUCT, {**product_body(account, offering), "productOffering": current})
    assert product.status_code == 201, product.text
    assert product.json()["productOffering"]["version"] == "2"


def test_patch_product_offering_places_a_modify_order(api, shop):
    offering, account = shop
    api.clock.set(NOW)  # prices cannot start in the past
    other = apidata.sellable_offering(api, value=600, name="Fiber 500")
    api.clock.set(at(OCT20))
    product = api.post(PRODUCT, product_body(account, offering)).json()
    api.clock.set(at(NOV1))
    response = api.patch(product["href"], {"productOffering": {"id": other["id"]}})
    assert response.status_code == 200, response.text
    assert response.json()["productOffering"]["id"] == other["id"]
    actions = sorted(o["productOrderItem"][0]["action"] for o in orders_of(api, account))
    assert actions == ["add", "modify"]
    unchanged = api.patch(product["href"], {"productOffering": {"id": other["id"]}})
    assert unchanged.status_code == 409


def test_account_and_start_date_only_repeat_their_value(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    other = apidata.account(api, name="Other")
    moved = api.patch(product["href"], {"billingAccount": {"id": other["id"]}})
    assert moved.status_code == 409 and "never changes" in moved.json()["message"]
    same = api.patch(product["href"], {"billingAccount": {"id": account["id"]}})
    assert same.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"status": "suspended", "terminationDate": "2026-11-11T00:00:00+03:00"},
        {"status": "suspended", "productOffering": {"id": apidata.NIL}},
    ],
)
def test_one_order_per_patch(api, shop, body):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    assert api.patch(product["href"], body).status_code == 422
    assert len(orders_of(api, account)) == 1
