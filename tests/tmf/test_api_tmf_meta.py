"""Nothing a TMF client sends is dropped or rewritten (docs/decisions/tmf-api.md,
"Nothing dropped or rewritten"): @baseType and @schemaLocation are stored and returned,
a client may choose the id, server-set attributes may be repeated but not set, a
payment keeps its method's name and @type apart, order items keep their order and
carry the order's account, and a customer's engagedParty may change while the customer
has no billing account."""

from datetime import date
from typing import Any

import pytest

from tests import apidata
from tests.apidata import CATALOG, CUSTOMER, NIL, ORDER, PARTY, PAYMENT, PRODUCT
from tests.builders.billing.billing_db import at

ACCOUNTS = "/tmf-api/accountManagement/v5"
META = {"@baseType": "Party", "@schemaLocation": "https://example.org/x.schema.json"}


def _orderable(api) -> dict[str, Any]:
    """An offering, with the clock moved past the start of its price and validity."""
    created = apidata.sellable_offering(api)
    api.clock.set(at(date(2026, 10, 20)))
    return created


def _owner(party: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "owner",
            "partyOrPartyRole": {"id": party["id"], "@referredType": party["@type"]},
            "@type": "RelatedPartyRefOrPartyRoleRef",
        }
    ]


# resource -> (collection URL, a valid create body; `api` given for what it references)
CREATES = {
    "individual": (f"{PARTY}/individual", lambda api: {"givenName": "A", "familyName": "B"}),
    "organization": (f"{PARTY}/organization", lambda api: {"name": "Romashka"}),
    "customer": (CUSTOMER, lambda api: apidata.customer_body(apidata.individual(api))),
    "financialAccount": (
        f"{ACCOUNTS}/financialAccount",
        lambda api: {"name": "F", "relatedParty": _owner(apidata.organization(api))},
    ),
    "partyAccount": (
        f"{ACCOUNTS}/partyAccount",
        lambda api: {"name": "P", "relatedParty": _owner(apidata.organization(api))},
    ),
    "billFormat": (f"{ACCOUNTS}/billFormat", lambda api: {"name": "JSON detailed"}),
    "billPresentationMedia": (f"{ACCOUNTS}/billPresentationMedia", lambda api: {"name": "email"}),
    "billingCycleSpecification": (
        f"{ACCOUNTS}/billingCycleSpecification",
        lambda api: {"name": "Business"},
    ),
    "productSpecification": (
        f"{CATALOG}/productSpecification",
        lambda api: {"name": "Fiber", "lifecycleStatus": "In Design"},
    ),
    "category": (f"{CATALOG}/category", lambda api: {"name": "Home"}),
    "productCatalog": (f"{CATALOG}/productCatalog", lambda api: {"name": "Retail"}),
    "productOrder": (
        ORDER,
        lambda api: apidata.order_body(apidata.account(api), apidata.add_item(_orderable(api))),
    ),
}


@pytest.mark.parametrize("resource", sorted(CREATES))
def test_base_type_and_schema_location_are_stored_returned_and_patchable(api, resource):
    url, body = CREATES[resource]
    created = api.post(url, {**body(api), **META})
    assert created.status_code == 201, created.text
    new = created.json()
    assert {k: new[k] for k in META} == META
    assert api.get(new["href"]).json() == new
    listed = [item for item in api.get(f"{url}?limit=100").json() if item["id"] == new["id"]]
    assert listed == [new]
    selected = api.get(f"{new['href']}?fields=@baseType,@schemaLocation").json()
    assert selected == {"id": new["id"], "href": new["href"], "@type": new["@type"], **META}
    patched = api.patch(
        new["href"], {"@schemaLocation": "https://example.org/y", "@baseType": None}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["@schemaLocation"] == "https://example.org/y"
    assert "@baseType" not in patched.json()
    assert api.get(new["href"]).json() == patched.json()


def test_an_empty_base_type_is_refused(api):
    response = api.post(f"{PARTY}/organization", {"name": "R", "@baseType": ""})
    assert response.status_code == 422
    assert api.get(f"{PARTY}/organization").json() == []


def test_server_made_resources_select_meta_without_a_value(api):
    for url in (apidata.BILL, apidata.RATE, "/tmf-api/customerBillManagement/v5/billCycle"):
        assert api.get(f"{url}?fields=@baseType,@schemaLocation").status_code == 200


def test_payments_and_products_keep_meta(api):
    acc = apidata.account(api)
    payment = api.post(PAYMENT, {**apidata.payment_body(acc), **META})
    assert payment.status_code == 201, payment.text
    assert {k: payment.json()[k] for k in META} == META
    offering = _orderable(api)
    body = {
        "billingAccount": {"id": acc["id"]},
        "productOffering": {"id": offering["id"]},
        "id": NIL,
        **META,
    }
    product = api.post(PRODUCT, body)
    assert product.status_code == 201, product.text
    assert product.json()["id"] == NIL and product.json()["href"].endswith(NIL)
    assert {k: product.json()[k] for k in META} == META
    assert api.get(product.json()["href"]).json() == product.json()
    assert api.post(PRODUCT, body).status_code == 409  # the id is taken, nothing ordered
    meta_only = api.patch(product.json()["href"], {"@baseType": None})
    assert meta_only.status_code == 200 and "@baseType" not in meta_only.json()


@pytest.mark.parametrize("resource", sorted(CREATES))
def test_a_client_may_choose_the_id(api, resource):
    url, make = CREATES[resource]
    body = make(api)
    chosen = "11111111-2222-3333-4444-555555555555"
    created = api.post(url, {**body, "id": chosen})
    assert created.status_code == 201, created.text
    assert created.json()["id"] == chosen and created.json()["href"].endswith(f"/{chosen}")
    assert created.headers["Location"] == created.json()["href"]
    assert api.get(created.json()["href"]).status_code == 200
    again = api.post(url, {**body, "id": chosen})
    assert again.status_code == 409 and "already in use" in again.json()["message"]
    assert api.post(url, {**body, "id": "abc"}).status_code == 422
    assert api.post(url, {**body, "id": 5}).status_code == 422
    assert [item["id"] for item in api.get(f"{url}?limit=100").json()].count(chosen) == 1


def test_customer_identity_is_accepted_unchanged(api):
    person = apidata.individual(api)
    chosen = "22222222-2222-3333-4444-555555555555"
    href = f"{CUSTOMER}/{chosen}"
    assert (
        api.post(CUSTOMER, apidata.customer_body(person, id=chosen, href=href)).status_code == 201
    )
    other_href = apidata.customer_body(person, href=f"{CUSTOMER}/{NIL}")
    assert api.post(CUSTOMER, other_href).status_code == 422  # not the new customer's href
    assert api.patch(href, {"id": chosen, "href": href, "name": "B"}).status_code == 200
    assert api.patch(href, {"id": NIL}).status_code == 422
    assert api.patch(href, {"href": "/x"}).status_code == 422
    assert api.get(href).json()["name"] == "B"


def test_engaged_party_changes_only_without_billing_accounts(api):
    ivan, anna = apidata.individual(api), apidata.individual(api, "Anna", "Petrova")
    customer = apidata.customer(api, ivan)
    same = api.patch(customer["href"], {"engagedParty": customer["engagedParty"]})
    assert same.status_code == 200
    wrong_type = {"id": anna["id"], "@referredType": "Organization"}
    assert api.patch(customer["href"], {"engagedParty": wrong_type}).status_code == 422
    moved = api.patch(customer["href"], {"engagedParty": {"id": anna["id"]}})
    assert moved.status_code == 200, moved.text
    assert moved.json()["engagedParty"]["id"] == anna["id"]
    apidata.account(api, moved.json())
    refused = api.patch(customer["href"], {"engagedParty": {"id": ivan["id"]}})
    assert refused.status_code == 409 and "billing accounts" in refused.json()["message"]
    unchanged = api.patch(customer["href"], {"engagedParty": {"id": anna["id"]}, "name": "C"})
    assert unchanged.status_code == 200
    assert api.get(customer["href"]).json()["engagedParty"]["id"] == anna["id"]


def test_order_items_keep_their_order_and_carry_the_order_account(api):
    acc = apidata.account(api)
    offering = _orderable(api)
    ids = ["2", "10", "1"]
    placed = apidata.order(
        api,
        acc,
        *(apidata.add_item(offering, i, billingAccount={"id": acc["id"]}) for i in ids),
    )
    assert placed["state"] == "completed"
    assert [item["id"] for item in placed["productOrderItem"]] == ids
    assert [item["billingAccount"]["id"] for item in placed["productOrderItem"]] == [acc["id"]] * 3
    assert api.get(placed["href"]).json() == placed


def test_server_set_order_attributes_may_be_repeated_but_not_set(api):
    acc = apidata.account(api)
    offering = _orderable(api)
    body = apidata.order_body(acc, apidata.add_item(offering))
    assert api.post(ORDER, {**body, "state": "completed"}).status_code == 201
    refused = api.post(ORDER, {**body, "state": "acknowledged"})
    assert refused.status_code == 422 and "state is set by the server" in refused.json()["message"]
    assert len(api.get(f"{ORDER}?billingAccount.id={acc['id']}").json()) == 1  # rolled back
