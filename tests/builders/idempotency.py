"""Shared builders of tests/test_idempotency.py, used by other test modules (#423)."""

from tests import apidata
from tests.builders.api_resource_inventory import number
from tests.builders.number_orders import refs


def body(api, off, acc, serial="1"):
    item = apidata.add_item(off, "1")
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value=f"7999555{serial}")["id"]),
    }
    return apidata.order_body(acc, item)
