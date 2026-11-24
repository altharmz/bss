"""Builders other test modules share, from tests/prepaid/test_prepaid_orders.py (#423)."""

from datetime import timedelta
from decimal import Decimal as D

from tests import apidata, timeline
from tests.apidata import ORDER
from tests.builders.billing.settlement_db import make_payment
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs


def shop(api, conn, funds="24", *, amount=10, numbers=True):
    off = apidata.sellable_offering(
        api, value=amount, tax=[{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]
    )
    if numbers:
        response = api.patch(
            off["productSpecification"]["href"],
            {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
        )
        assert response.status_code == 200, response.text
    acc = apidata.account(api)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    if D(funds):
        make_payment(conn, dict(acc, currency="USD"), funds)
    conn.commit()
    api.clock.set(timeline.START + timedelta(hours=9))
    return off, acc


def purchase(api, off, acc, *, nested=False, numbers=True, status=201):
    items = []
    for i in range(2):
        item = apidata.add_item(off, str(i + 1))
        if numbers:
            item["product"] = {
                "@type": "ProductRefOrValue",
                "realizingResource": refs(number(api, value=f"7999123400{i}")["id"]),
            }
        items.append(item)
    if nested:
        items[0]["productOrderItem"] = [items.pop()]
    response = api.post(ORDER, apidata.order_body(acc, *items))
    assert response.status_code == status, response.text
    return response.json()
