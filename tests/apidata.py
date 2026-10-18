"""Builders that create resources through the API (never the repository), for API tests.
Each asserts the 201 and returns the response body."""

from typing import Any

from tests import timeline
from tests.timeline import iso

CATALOG = "/tmf-api/productCatalogManagement/v5"
PARTY = "/tmf-api/partyManagement/v5"
CUSTOMER = "/tmf-api/customer/v5/customer"
ACCOUNT = "/tmf-api/accountManagement/v5/billingAccount"
PAYMENT = "/tmf-api/payment/v4/payment"
PRODUCT = "/tmf-api/productInventory/v5/product"
BILL = "/tmf-api/customerBillManagement/v5/customerBill"
RATE = "/tmf-api/customerBillManagement/v5/appliedCustomerBillingRate"
ORDER = "/tmf-api/productOrderingManagement/v5/productOrder"

NIL = "00000000-0000-0000-0000-000000000000"
START = iso(timeline.START)


def _post(api, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = api.post(url, body)
    assert response.status_code == 201, response.text
    return response.json()


def individual(api, given="Ivan", family="Ivanov") -> dict[str, Any]:
    return _post(
        api,
        f"{PARTY}/individual",
        {"@type": "Individual", "givenName": given, "familyName": family},
    )


def organization(api, name="Romashka LLC") -> dict[str, Any]:
    return _post(api, f"{PARTY}/organization", {"@type": "Organization", "name": name})


def customer_body(party: dict[str, Any], **over) -> dict[str, Any]:
    return {
        "@type": "Customer",
        "name": "Ivan Ivanov",
        "engagedParty": {
            "id": party["id"],
            "href": party["href"],
            "@type": "PartyRef",
            "@referredType": party["@type"],
        },
        **over,
    }


def customer(api, party=None, **over) -> dict[str, Any]:
    return _post(api, CUSTOMER, customer_body(party or individual(api), **over))


def account_body(cust: dict[str, Any], **over) -> dict[str, Any]:
    return {
        "@type": "BssBillingAccount",
        "@baseType": "BillingAccount",
        "name": "Main account",
        "billCycleDay": 1,
        "currency": "RUB",
        "relatedParty": [
            {
                "@type": "RelatedPartyRefOrPartyRoleRef",
                "role": "customer",
                "partyOrPartyRole": {
                    "id": cust["id"],
                    "href": cust["href"],
                    "name": cust["name"],
                    "@type": "PartyRoleRef",
                    "@referredType": "Customer",
                },
            }
        ],
        **over,
    }


def account(api, cust=None, **over) -> dict[str, Any]:
    return _post(api, ACCOUNT, account_body(cust or customer(api), **over))


def payment_body(acc: dict[str, Any], value=1500, unit="RUB", **over) -> dict[str, Any]:
    return {
        "@type": "Payment",
        "account": {"id": acc["id"], "href": acc["href"], "@referredType": "BillingAccount"},
        "totalAmount": {"unit": unit, "value": value},
        "paymentMethod": {"name": "bankTransfer", "@type": "BankAccountTransfer"},
        **over,
    }


def payment(api, acc: dict[str, Any], value=1500, **over) -> dict[str, Any]:
    return _post(api, PAYMENT, payment_body(acc, value, **over))


def code_identifier(code: str) -> list[dict[str, Any]]:
    """An offering code as TMF620 externalIdentifier."""
    return [{"id": code, "externalIdentifierType": "offeringCode", "@type": "ExternalIdentifier"}]


def tax_item(category: str, rate, **over) -> dict[str, Any]:
    """A TMF620 TaxItem: a category and a rate in percent."""
    return {"@type": "TaxItem", "taxCategory": category, "taxRate": rate, **over}


def sellable_offering(
    api,
    value=600,
    unit="RUB",
    name="Fiber 100",
    code: str | None = None,
    tax: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A specification, a monthly recurring price (with `tax` items, if given) and an
    Active offering carrying it."""
    spec = _post(
        api,
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Fiber", "lifecycleStatus": "Active"},
    )
    price = _post(
        api,
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": unit, "value": value},
            "validFor": {"startDateTime": START},
            **({} if tax is None else {"tax": tax}),
        },
    )
    return _post(
        api,
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": name,
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": START},
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [{"id": price["id"], "@type": "ProductOfferingPriceRef"}],
            **({} if code is None else {"externalIdentifier": code_identifier(code)}),
        },
    )


def add_item(offering: dict[str, Any], item_id: str = "1", **over) -> dict[str, Any]:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "add",
        "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
        **over,
    }


def rejected_item(api, offering: dict[str, Any], item_id: str = "1") -> dict[str, Any]:
    """An add item that is always rejected (billingAccountMismatch: it names another
    account than the order's) - for tests that need a rejected item or order."""
    elsewhere = account(api, name="Elsewhere")
    return add_item(
        offering, item_id, billingAccount={"id": elsewhere["id"], "@type": "BillingAccountRef"}
    )


def delete_item(product_id: str, item_id: str = "1", **over) -> dict[str, Any]:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "delete",
        "product": {"id": product_id, "@type": "ProductRef"},
        **over,
    }


def order_body(acc: dict[str, Any], *items: dict[str, Any], **over) -> dict[str, Any]:
    return {
        "@type": "ProductOrder",
        "billingAccount": {"id": acc["id"], "@type": "BillingAccountRef"},
        "productOrderItem": list(items),
        **over,
    }


def order(api, acc: dict[str, Any], *items: dict[str, Any], **over) -> dict[str, Any]:
    return _post(api, ORDER, order_body(acc, *items, **over))


def product_of(api, placed: dict[str, Any], item_id: str = "1") -> dict[str, Any]:
    """The product an add item of a completed order created."""
    (item,) = [i for i in placed["productOrderItem"] if i["id"] == item_id]
    response = api.get(item["product"]["href"])
    assert response.status_code == 200
    return response.json()
