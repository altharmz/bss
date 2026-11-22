"""Shared builders of tests/test_api_order_cancel.py, used by other test modules (#423)."""

from datetime import date
from decimal import Decimal

import pytest

from app.worker import db as worker_db
from app.worker.process import bill_date
from tests import apidata
from tests.builders.billing_db import at
from tests.timeline import TZ

BASE = "/tmf-api/productOrderingManagement/v5"

CANCEL = f"{BASE}/cancelProductOrder"


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def run(worker, api, day: date) -> None:
    api.clock.set(at(day, 0))
    bill_date(worker, day, clock=api.clock, tz=TZ, trigger="scheduled")


def cancel_body(order: dict, **over) -> dict:
    return {"@type": "CancelProductOrder", "productOrder": {"id": order["id"]}, **over}


def order_of(api, product: dict, role: str) -> dict:
    (related,) = [r for r in product["productOrderItem"] if r["role"] == role]
    return api.get(related["orderHref"]).json()


def refused(api, order: dict, *expected: str, stored: int = 0) -> None:
    """A 409 naming `expected`; the order unchanged and no (further) cancellation stored."""
    before = api.get(order["href"]).json()
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 409, response.text
    for text in expected:
        assert text in response.json()["message"], response.json()["message"]
    assert api.get(order["href"]).json() == before
    assert len(api.get(f"{CANCEL}?productOrder.id={order['id']}").json()) == stored


def rates(api, product: dict) -> list[tuple[str, Decimal]]:
    listed = api.get(f"{apidata.RATE}?product.id={product['id']}").json()
    return [
        (r["periodCoverage"]["startDateTime"], Decimal(str(r["taxExcludedAmount"]["value"])))
        for r in listed
    ]
