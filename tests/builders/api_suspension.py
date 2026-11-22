"""Shared builders of tests/test_api_suspension.py, used by other test modules (#423)."""

from datetime import date

from tests import apidata
from tests.timeline import iso, midnight

OCT2, OCT20, NOV1, NOV5, NOV11 = (
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 1),
    date(2026, 11, 5),
    date(2026, 11, 11),
)


def status_item(product: dict, status: str, item_id: str = "1", **over) -> dict:
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "modify",
        "product": {"id": product["id"], "status": status, "@type": "ProductRef"},
        **over,
    }


def change(api, acc, product, status: str, day: date | None, *, on) -> dict:
    api.clock.set(on)
    over = {} if day is None else {"requestedStartDate": iso(midnight(day))}
    return apidata.order(api, acc, status_item(product, status), **over)
