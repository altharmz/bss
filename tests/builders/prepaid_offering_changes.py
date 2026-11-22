"""Shared builders of tests/test_prepaid_offering_changes.py, used by other test modules (#423)."""

from datetime import datetime, time
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.worker import loop
from tests import apidata
from tests.apidata import ORDER
from tests.builders.api_order_modify import modify_item
from tests.builders.number_orders import refs
from tests.builders.prepaid_orders import purchase, shop
from tests.builders.prepaid_posting import TZ
from tests.builders.prepaid_renewal_producer import tick


@pytest.fixture
def granted(api, db_conn, monkeypatch):
    """Two funded products on plan A (10 + 20% VAT), no money left, and plan B (5)."""
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    cheaper = apidata.sellable_offering(
        api, value=5, tax=[{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]
    )
    response = api.patch(
        cheaper["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    off, acc = shop(api, db_conn)
    order = purchase(api, off, acc)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    rows = db_conn.execute(
        "SELECT product_id, id, upper(period) FROM funded_interval ORDER BY product_id"
    ).fetchall()
    products = [{"id": str(r[0]), "grant": r[1], "due": r[2]} for r in rows]
    return SimpleNamespace(acc=acc, a=off, b=cheaper, products=products)


def at(day, hour=9):
    return datetime.combine(day, time(hour), TZ)


def switch(api, acc, product, offering, day, number_id=None):
    item = modify_item(product, offering)
    if number_id is not None:
        item["product"]["realizingResource"] = refs(number_id)
    body = apidata.order_body(acc, item, requestedStartDate=at(day).isoformat())
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def offering_of(api, product):
    return api.get(f"{apidata.PRODUCT}/{product['id']}").json()["productOffering"]["id"]


def applied(conn, product):
    return conn.execute(
        "SELECT effect_announced_at IS NOT NULL FROM product_offering_change WHERE product_id = %s",
        (product["id"],),
    ).fetchone()[0]
