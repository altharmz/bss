"""Order items refused for the product they name: a number or device change of another
account's product, of an ended one or before it starts, an end before a future number
change, and a prepaid-only offering on a postpaid account. Each is a rejected order that
changed nothing."""

from datetime import timedelta

import pytest

from tests import apidata, timeline
from tests.builders.prepaid.prepaid_plans import offering, price
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs

IMEI = "490154203237518"


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api)
    response = api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    api.clock.set(timeline.START + timedelta(hours=9))
    return offering, apidata.account(api)


def day(days):
    return timeline.iso(timeline.midnight(timeline.TOMORROW + timedelta(days=days)))


def line(api, shop, value="12025550123", **over):
    offering, acc = shop
    item = apidata.add_item(offering)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value)["id"]),
    }
    placed = apidata.order(api, acc, item, **over)
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def renumber(product, uid):
    return {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "modify",
        "product": {"id": product["id"], "@type": "ProductRef", "realizingResource": refs(uid)},
    }


def rejected(api, acc, item, code, reason, **over):
    placed = apidata.order(api, acc, item, **over)
    assert placed["state"] == "rejected", placed
    assert (code, reason) in [(e["code"], e["reason"]) for e in placed["productOrderErrorMessage"]]
    return placed


def test_a_number_change_names_a_live_started_product_of_the_account(api, shop):
    _, acc = shop
    product = line(api, shop)
    other = apidata.account(api, name="Other")
    spare = number(api, "12025550124")
    free = spare["id"]
    rejected(
        api,
        other,
        renumber(product, free),
        "productOfOtherAccount",
        "the product belongs to another billing account",
    )
    future = line(api, shop, "12025550125", requestedStartDate=day(5))
    rejected(
        api,
        acc,
        renumber(future, free),
        "productNotStarted",
        "number change precedes product start",
        requestedStartDate=day(2),
    )
    ended = apidata.order(api, acc, apidata.delete_item(product["id"]))
    assert ended["state"] == "completed"
    rejected(api, acc, renumber(product, free), "productAlreadyEnded", "the product has ended")
    assert api.get(spare["href"]).json()["allocationStatus"] == "available"


def test_a_product_with_a_future_number_change_is_not_ended(api, shop):
    _, acc = shop
    product = line(api, shop)
    free = number(api, "12025550124")["id"]
    changed = apidata.order(api, acc, renumber(product, free), requestedStartDate=day(5))
    assert changed["state"] == "completed"
    rejected(
        api,
        acc,
        apidata.delete_item(product["id"]),
        "openModifyItem",
        "cancel the future number change before ending this product",
        requestedStartDate=day(2),
    )
    assert "terminationDate" not in api.get(product["href"]).json()


def test_a_device_change_names_a_product_of_the_account(api, shop):
    product = line(api, shop)
    other = apidata.account(api, name="Other")
    item = {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "modify",
        "product": {
            "id": product["id"],
            "@type": "ProductRef",
            "productCharacteristic": [
                {"@type": "StringCharacteristic", "name": "imei", "value": IMEI}
            ],
        },
    }
    rejected(
        api,
        other,
        item,
        "productOfOtherAccount",
        "the product belongs to another billing account",
    )


def test_a_pay_as_you_go_offering_is_for_prepaid_accounts(api, shop):
    _, acc = shop
    api.clock.set(timeline.START - timedelta(hours=1))  # its price starts at START
    payg = offering(api, "Pay as you go", [price(api, "oneTime", 2, "SIM")], kind="payAsYouGo")
    api.clock.set(timeline.START + timedelta(hours=9))
    rejected(
        api,
        acc,
        apidata.add_item(payg),
        "prepaidOnly",
        "a payAsYouGo offering is for prepaid accounts",
    )
