"""Shared builders of tests/test_number_orders.py, used by other test modules (#423)."""

from tests.builders.api_order_cancel import CANCEL


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
