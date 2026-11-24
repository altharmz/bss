"""Builders other test modules share, from tests/prepaid/test_prepaid_status_orders.py (#423)."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from psycopg import sql

from app.worker import loop
from tests import apidata
from tests.apidata import ORDER
from tests.builders.orders.api_suspension import status_item
from tests.builders.prepaid.prepaid_orders import purchase, shop
from tests.builders.prepaid.prepaid_renewal_producer import tick


@pytest.fixture
def granted(api, db_conn, monkeypatch):
    """Two funded products of one prepaid account, no money left."""
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = shop(api, db_conn, numbers=False)
    order = purchase(api, off, acc, numbers=False)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    rows = db_conn.execute(
        "SELECT product_id, id, upper(period) FROM funded_interval ORDER BY product_id"
    ).fetchall()
    return acc, [{"id": str(r[0]), "grant": r[1], "due": r[2]} for r in rows]


def change(api, acc, product, status, **over):
    response = api.post(ORDER, apidata.order_body(acc, status_item(product, status), **over))
    assert response.status_code == 201, response.text
    return response.json()


def errors(order):
    return [e["code"] for e in order.get("productOrderErrorMessage", [])]


def view(api, acc, product):
    products = api.get(f"/internal/v1/billingAccount/{acc['id']}/products").json()
    return next(p for p in products if p["id"] == product["id"])


def count(conn, table):
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    return conn.execute(query).fetchone()[0]
