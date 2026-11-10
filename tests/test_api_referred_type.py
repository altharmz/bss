"""`@referredType` in a request (doc-findings #17). TMF630 / the vendored specs' EntityRef:
"The actual type of the target instance when needed for disambiguation". Optional; when
sent it must be the type the response states - a contradicting one is a 422 and nothing
is stored, never accepted and answered with another value (app/api/common.py, `Ref`)."""

from datetime import timedelta
from typing import Any

import pytest

from tests import apidata, timeline
from tests.apiclient import total
from tests.apidata import ACCOUNT, CATALOG, CUSTOMER, ORDER, PAYMENT

# orders: the day the catalog's prices start (as in test_api_ordering.py)
OPEN = timeline.START + timedelta(hours=9)


def _count(api, url: str) -> int:
    return int(total(api.get(f"{url}?limit=1"), api))


def _refused(api, url: str, body: dict[str, Any], field: str, expected: str) -> None:
    before = _count(api, url)
    response = api.post(url, body)
    assert response.status_code == 422, response.text
    message = response.json()["message"]
    assert f"{field}.@referredType: " in message and f"must be {expected} " in message
    assert _count(api, url) == before


def _offering_body(spec_ref: dict[str, Any], price_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "@type": "ProductOffering",
        "name": "Mobile S",
        "lifecycleStatus": "In Design",
        "validFor": {"startDateTime": apidata.START},
        "productSpecification": spec_ref,
        "productOfferingPrice": [price_ref],
    }


def _spec_and_price(api) -> tuple[dict[str, Any], dict[str, Any]]:
    """A specification and a free price of a new sellable offering's chain (its own
    price is attached; a fresh one is needed for another offering)."""
    off = apidata.sellable_offering(api)
    spec = api.get(off["productSpecification"]["href"]).json()
    price = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 300},
            "validFor": {"startDateTime": apidata.START},
        },
    ).json()
    return spec, price


# --- the right type: accepted, and the response states exactly what was sent ---


@pytest.mark.parametrize("party_kind", ["Individual", "Organization"])
def test_engaged_party_with_its_actual_type_is_answered_as_sent(api, party_kind):
    party = apidata.individual(api) if party_kind == "Individual" else apidata.organization(api)
    sent = apidata.customer_body(party)
    assert sent["engagedParty"]["@referredType"] == party_kind
    created = api.post(CUSTOMER, sent)
    assert created.status_code == 201, created.text
    assert created.json()["engagedParty"]["@referredType"] == party_kind


def test_fixed_type_references_are_answered_as_sent(api):
    acc = apidata.account(api)
    assert acc["relatedParty"][0]["partyOrPartyRole"]["@referredType"] == "Customer"
    paid = apidata.payment(api, acc)  # account @referredType: BillingAccount
    assert paid["account"]["@referredType"] == "BillingAccount"

    spec, price = _spec_and_price(api)
    created = api.post(
        f"{CATALOG}/productOffering",
        _offering_body(
            {"id": spec["id"], "@referredType": "ProductSpecification"},
            {"id": price["id"], "@referredType": "ProductOfferingPrice"},
        ),
    )
    assert created.status_code == 201, created.text
    assert created.json()["productSpecification"]["@referredType"] == "ProductSpecification"
    assert created.json()["productOfferingPrice"][0]["@referredType"] == "ProductOfferingPrice"

    off = apidata.sellable_offering(api, name="Fiber 200")
    api.clock.set(OPEN)
    placed = apidata.order(
        api,
        acc,
        apidata.add_item(
            off,
            productOffering={"id": off["id"], "@referredType": "ProductOffering"},
            billingAccount={"id": acc["id"], "@referredType": "BillingAccount"},
        ),
        billingAccount={"id": acc["id"], "@referredType": "BillingAccount"},
    )
    assert placed["billingAccount"]["@referredType"] == "BillingAccount"
    assert placed["productOrderItem"][0]["productOffering"]["@referredType"] == "ProductOffering"
    product = apidata.product_of(api, placed)
    api.clock.set(OPEN + timedelta(days=10))
    ended = apidata.order(
        api,
        acc,
        apidata.delete_item(
            product["id"], product={"id": product["id"], "@referredType": "Product"}
        ),
    )
    assert ended["productOrderItem"][0]["product"]["@referredType"] == "Product"


def test_no_referred_type_is_still_accepted(api):
    party = apidata.individual(api)
    body = apidata.customer_body(party)
    del body["engagedParty"]["@referredType"]
    created = api.post(CUSTOMER, body)
    assert created.status_code == 201, created.text
    assert created.json()["engagedParty"]["@referredType"] == "Individual"


# --- a contradicting type: 422, nothing stored ---


def test_engaged_party_of_the_other_party_type_is_refused(api):
    person, org = apidata.individual(api), apidata.organization(api)
    for party, wrong, actual in (
        (person, "Organization", "Individual"),
        (org, "Individual", "Organization"),
    ):
        body = apidata.customer_body(party)
        body["engagedParty"]["@referredType"] = wrong
        _refused(api, CUSTOMER, body, "engagedParty", actual)
    body = apidata.customer_body(person)
    body["engagedParty"]["@referredType"] = "Customer"
    _refused(api, CUSTOMER, body, "engagedParty", "Individual")


def test_account_party_that_is_not_a_customer_is_refused(api):
    body = apidata.account_body(apidata.customer(api))
    body["relatedParty"][0]["partyOrPartyRole"]["@referredType"] = "Individual"
    _refused(api, ACCOUNT, body, "relatedParty.0.partyOrPartyRole", "Customer")


def test_payment_account_that_is_not_a_billing_account_is_refused(api):
    acc = apidata.account(api)
    body = apidata.payment_body(acc, account={"id": acc["id"], "@referredType": "Customer"})
    _refused(api, PAYMENT, body, "account", "BillingAccount")


def test_offering_references_of_another_type_are_refused(api):
    spec, price = _spec_and_price(api)
    url = f"{CATALOG}/productOffering"
    good_spec = {"id": spec["id"]}
    good_price = {"id": price["id"]}
    _refused(
        api,
        url,
        _offering_body({**good_spec, "@referredType": "ProductOffering"}, good_price),
        "productSpecification",
        "ProductSpecification",
    )
    _refused(
        api,
        url,
        _offering_body(good_spec, {**good_price, "@referredType": "ProductOffering"}),
        "productOfferingPrice.0",
        "ProductOfferingPrice",
    )
    # PATCH: the same model, and the price stays unattached
    off = api.post(url, {**_offering_body(good_spec, good_price), "productOfferingPrice": []})
    assert off.status_code == 201, off.text
    response = api.patch(
        off.json()["href"],
        {"productOfferingPrice": [{**good_price, "@referredType": "Price"}]},
    )
    assert response.status_code == 422, response.text
    assert "productOfferingPrice.0.@referredType: " in response.json()["message"]
    assert api.get(off.json()["href"]).json()["productOfferingPrice"] == []


def test_order_references_of_another_type_are_refused(api):
    acc = apidata.account(api)
    off = apidata.sellable_offering(api)
    wrong_account = {"id": acc["id"], "@referredType": "Customer"}
    _refused(
        api,
        ORDER,
        apidata.order_body(acc, apidata.add_item(off), billingAccount=wrong_account),
        "billingAccount",
        "BillingAccount",
    )
    _refused(
        api,
        ORDER,
        apidata.order_body(acc, apidata.add_item(off, billingAccount=wrong_account)),
        "productOrderItem.0.billingAccount",
        "BillingAccount",
    )
    _refused(
        api,
        ORDER,
        apidata.order_body(
            acc,
            apidata.add_item(
                off, productOffering={"id": off["id"], "@referredType": "ProductSpecification"}
            ),
        ),
        "productOrderItem.0.productOffering",
        "ProductOffering",
    )
    api.clock.set(OPEN)
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(off)))
    _refused(
        api,
        ORDER,
        apidata.order_body(
            acc,
            apidata.delete_item(
                product["id"], product={"id": product["id"], "@referredType": "ProductOffering"}
            ),
        ),
        "productOrderItem.0.product",
        "Product",
    )
