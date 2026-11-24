"""Builders other test modules share, from tests/resources/test_number_orders.py (#423)."""

from tests.builders.orders.api_order_cancel import CANCEL


def refs(uid):
    return [{"id": uid, "@type": "ResourceRef", "@referredType": "LogicalResource"}]


def cancel(api, order):
    return api.post(
        CANCEL,
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": order["id"], "@type": "ProductOrderRef"},
        },
    )
