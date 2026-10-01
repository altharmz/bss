"""The write requests the admin UI sends (frontend/src/api/bss.ts), replayed verbatim
against the real API and its contract checks. The UI's component tests pin these bodies
on the frontend side; this pins that the backend accepts them and stores what the UI
means. If the UI changes a body, change it here too.

Context: business date 2026-10-01 in Europe/Moscow (+03:00), the api fixture's clock.
"""

from decimal import Decimal

from tests import apidata
from tests.apiclient import decimals
from tests.apidata import CATALOG, ORDER
from tests.timeline import TZ

OFFSET = "+03:00"


def spec(api) -> dict:
    return api.post(
        f"{CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Mobile Voice & Data",
            "lifecycleStatus": "Active",
        },
    ).json()


def create_offering(api, code: str = "MOB-XL") -> dict:
    """createOffering(): the price (amount as a raw number token), then the offering."""
    valid_for = (
        '{"startDateTime":"2026-10-02T00:00:00+03:00","endDateTime":"2027-01-01T00:00:00+03:00"}'
    )
    price = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        '{"@type":"ProductOfferingPrice","name":"Mobile XL monthly","lifecycleStatus":"Active",'
        '"priceType":"recurring","recurringChargePeriodType":"monthly",'
        f'"price":{{"unit":"RUB","value":1990.50}},"validFor":{valid_for}}}',
    )
    assert price.status_code == 201, price.text
    offering = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Mobile XL",
            "lifecycleStatus": "In Design",
            "validFor": {
                "startDateTime": "2026-10-02T00:00:00+03:00",
                "endDateTime": "2027-01-01T00:00:00+03:00",
            },
            "productSpecification": {"id": spec(api)["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [
                {"id": price.json()["id"], "@type": "ProductOfferingPriceRef"}
            ],
            "externalIdentifier": [
                {
                    "id": code,
                    "externalIdentifierType": "offeringCode",
                    "@type": "ExternalIdentifier",
                }
            ],
        },
    )
    assert offering.status_code == 201, offering.text
    return offering.json()


def test_new_offering_is_stored_as_the_ui_means_it(api):
    offering = create_offering(api)
    assert offering["lifecycleStatus"] == "In Design"
    assert offering["externalIdentifier"][0]["id"] == "MOB-XL"
    # "valid to 2026-12-31" (inclusive) is stored as the exclusive midnight of Jan 1
    assert offering["validFor"] == {
        "startDateTime": "2026-10-01T21:00:00Z",
        "endDateTime": "2026-12-31T21:00:00Z",
    }
    price = decimals(api.get(offering["productOfferingPrice"][0]["href"]))
    assert price["price"] == {"unit": "RUB", "value": Decimal("1990.50")}


def test_lifecycle_steps_are_merge_patches(api):
    offering = create_offering(api)
    for status in ("Active", "Retired"):  # NEXT_LIFECYCLE
        response = api.patch(offering["href"], {"lifecycleStatus": status})
        assert response.status_code == 200, response.text
        assert response.json()["lifecycleStatus"] == status


def test_add_and_delete_orders_as_the_ui_sends_them(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))  # prices are valid from Oct 2
    # orderAdd(): requestedStartDate is noon of the chosen day in BILLING_TZ
    added = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": f"2026-10-06T12:00:00{OFFSET}",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                }
            ],
        },
    )
    assert added.status_code == 201 and added.json()["state"] == "completed", added.text
    product = apidata.product_of(api, added.json())
    started = product["startDate"]
    # midnight of 2026-10-06 in Moscow: the day the operator chose, not a day off
    assert started == "2026-10-05T21:00:00Z"

    # orderDelete(): no requestedStartDate = today; a product not started yet is cancelled
    deleted = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "delete",
                    "product": {"id": product["id"], "@type": "ProductRef"},
                }
            ],
        },
    )
    assert deleted.status_code == 201 and deleted.json()["state"] == "completed", deleted.text
    assert api.get(product["href"]).json()["status"] == "cancelled"
    assert TZ.key == "Europe/Moscow"  # the offsets above assume it


def test_price_change_as_the_ui_sends_it(api):
    """changePrice(): the amount as a raw number token, the date as a business date."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    assert apidata.order(api, account, apidata.add_item(offering))["state"] == "completed"
    response = api.post_raw(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        '{"amount":1390.00,"currency":"RUB","validFrom":"2026-11-01"}',
    )
    assert response.status_code == 201, response.text
    assert decimals(response)["newPrice"]["price"] == {"unit": "RUB", "value": Decimal("1390.00")}
