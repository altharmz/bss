"""Shared builders of tests/test_api_order_modify.py, used by other test modules (#423)."""

from datetime import date

from tests import apidata
from tests.timeline import iso, midnight


def modify_item(product: dict, offering: dict, item_id: str = "1", **over) -> dict:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "modify",
        "product": {"id": product["id"], "@type": "ProductRef"},
        "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
        **over,
    }


def modify(api, acc, product, offering, day: date | None, *, on) -> dict:
    api.clock.set(on)
    over = {} if day is None else {"requestedStartDate": iso(midnight(day))}
    return apidata.order(api, acc, modify_item(product, offering), **over)


def errors_of(order: dict) -> list[str]:
    return [e["code"] for e in order.get("productOrderErrorMessage", [])]
