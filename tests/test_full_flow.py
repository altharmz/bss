"""The whole stage 2 surface, end to end, through the API only: catalog, party, customer,
account, an order that adds a product, one that deletes it, a payment reflected in the
balance, and a modify order of the ended product stored as rejected. Every response is
contract-checked."""

from datetime import timedelta
from decimal import Decimal

from tests import apidata, timeline
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, CATALOG, ORDER, PAYMENT, PRODUCT, RATE
from tests.timeline import iso, midnight, z


def test_full_flow(api):
    # catalog: specification, a monthly price from the next midnight, an offering
    spec = api.post(
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Fiber", "lifecycleStatus": "Active"},
    ).json()
    price = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Fiber 100 monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "RUB", "value": 599.90},
            "validFor": {"startDateTime": iso(timeline.START)},
        },
    ).json()
    offering = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Fiber 100",
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": iso(timeline.START)},
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [{"id": price["id"], "@type": "ProductOfferingPriceRef"}],
        },
    ).json()
    assert offering["productOfferingPrice"][0]["id"] == price["id"]

    # party, customer, billing account
    person = apidata.individual(api, "Ivan", "Ivanov")
    customer = apidata.customer(api, person, name="Ivan Ivanov", status="Active")
    account = apidata.account(api, customer, name="Ivanov home", billCycleDay=5)
    assert account["relatedParty"][0]["partyOrPartyRole"]["id"] == customer["id"]

    # the next day, the price is valid: an add order creates an active product
    day1 = timeline.TOMORROW
    api.clock.set(midnight(day1) + timedelta(hours=10))
    added = apidata.order(api, account, apidata.add_item(offering))
    assert added["state"] == "completed"
    product = apidata.product_of(api, added)
    assert (product["status"], product["startDate"]) == ("active", z(midnight(day1)))
    listed = api.get(f"{PRODUCT}?billingAccount.id={account['id']}").json()
    assert [p["id"] for p in listed] == [product["id"]]

    # two weeks later a delete order terminates it
    day15 = day1 + timedelta(days=14)
    api.clock.set(midnight(day15) + timedelta(hours=10))
    deleted = apidata.order(api, account, apidata.delete_item(product["id"]))
    assert deleted["state"] == "completed"
    ended = api.get(product["href"]).json()
    assert (ended["status"], ended["terminationDate"]) == ("terminated", z(midnight(day15)))
    assert [i["orderId"] for i in ended["productOrderItem"]] == [added["id"], deleted["id"]]

    # a payment; no billing run yet, so the balance is exactly the payment
    paid = apidata.payment(api, account, 1000)
    assert api.get(f"{PAYMENT}?account.id={account['id']}").json() == [paid]
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    assert balance["amount"] == {"unit": "RUB", "value": Decimal("1000.00")}
    assert api.get(RATE).json() == []  # stage 2 never writes an ABR

    # a modify order of the ended product is stored as rejected, with its reason
    modify = {
        **apidata.delete_item(product["id"]),
        "action": "modify",
        "productOffering": {"id": product["productOffering"]["id"], "@type": "ProductOfferingRef"},
    }
    response = api.post(ORDER, apidata.order_body(account, modify))
    assert response.status_code == 201
    rejected = response.json()
    assert rejected["state"] == "rejected"
    assert rejected["productOrderErrorMessage"][0]["code"] == "productAlreadyEnded"
    assert api.get(rejected["href"]).json() == rejected

    states = [o["state"] for o in api.get(f"{ORDER}?billingAccount.id={account['id']}").json()]
    assert sorted(states) == ["completed", "completed", "rejected"]
    assert api.checked >= 18  # every one of these responses was contract-checked
