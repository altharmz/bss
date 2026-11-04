"""Prepaid offering changes take effect only through a funded renewal (P3h)."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from alembic.config import Config

from alembic import command
from app.repository import orders
from app.repository import prepaid_commands as commands
from app.worker import loop
from tests import apidata
from tests.apidata import ORDER
from tests.factories import world
from tests.test_api_order_cancel import CANCEL, cancel_body
from tests.test_api_order_modify import modify_item
from tests.test_api_resource_inventory import number
from tests.test_events import stored, subscribe
from tests.test_number_orders import refs
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_posting import TZ
from tests.test_prepaid_renewal_producer import pay, tick
from tests.test_prepaid_status_orders import errors


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


def renewed(conn, product):
    return conn.execute(
        "SELECT quote->>'offering_id', amount, tax_amount, lower(period) FROM funded_interval"
        " WHERE product_id = %s AND command_kind = 'renewal'",
        (product["id"],),
    ).fetchone()


def applied(conn, product):
    return conn.execute(
        "SELECT effect_announced_at IS NOT NULL FROM product_offering_change WHERE product_id = %s",
        (product["id"],),
    ).fetchone()[0]


def test_change_at_paid_through_applies_with_the_funded_renewal(api, db_conn, granted):
    first, second = granted.products
    order = switch(api, granted.acc, first, granted.b, first["due"])
    assert order["state"] == "completed" and errors(order) == []
    assert offering_of(api, first) == granted.a["id"]
    assert not applied(db_conn, first)

    pay(db_conn, UUID(granted.acc["id"]), "18", api.clock.now())  # 6 for B, 12 for A
    subscribe(api, "TMF637", "ProductAttributeValueChangeEvent")
    tick(db_conn, at(first["due"], 0))
    assert renewed(db_conn, first) == (granted.b["id"], D("5.00"), D("1.00"), first["due"])
    assert renewed(db_conn, second) == (granted.a["id"], D("10.00"), D("2.00"), second["due"])
    assert applied(db_conn, first)
    api.clock.set(at(first["due"]))
    assert offering_of(api, first) == granted.b["id"]
    events = stored(db_conn, "TMF637")
    assert [e["event"]["product"]["id"] for _, e in events] == [first["id"]]


def test_change_after_the_paid_through_date_is_rejected(api, db_conn, granted):
    first, _ = granted.products
    day = first["due"] + timedelta(days=1)
    order = switch(api, granted.acc, first, granted.b, day)
    assert order["state"] == "rejected" and errors(order) == ["offeringChangeNotAtRenewal"]
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)


def test_unfunded_change_stays_pending_without_old_plan_fallback(api, db_conn, granted):
    first, _ = granted.products
    switch(api, granted.acc, first, granted.b, first["due"])
    tick(db_conn, at(first["due"], 0))
    renewal = commands.renewal_id(first["grant"])
    shortfall = commands.needs_topup(db_conn, renewal)
    assert (shortfall["required"], shortfall["available"]) == (D("6.00"), D(0))  # plan B
    assert renewed(db_conn, first) is None and not applied(db_conn, first)
    api.clock.set(at(first["due"]))
    status = api.get(f"{apidata.PRODUCT}/{first['id']}").json()["status"]
    assert status == "suspended"

    later = at(first["due"] + timedelta(days=3))
    pay(db_conn, UUID(granted.acc["id"]), "6", later)
    assert renewal in tick(db_conn, later).prepaid_commands
    assert renewed(db_conn, first) == (granted.b["id"], D("5.00"), D("1.00"), later.date())
    assert applied(db_conn, first)


def test_expired_product_changes_today_and_resumes_on_the_new_plan(api, db_conn, granted):
    first, _ = granted.products
    later = at(first["due"] + timedelta(days=2))
    tick(db_conn, later)
    api.clock.set(later)
    at_due = switch(api, granted.acc, first, granted.b, first["due"])
    assert errors(at_due) == ["requestedStartDateInPast"]
    order = switch(api, granted.acc, first, granted.b, later.date())
    assert order["state"] == "completed", errors(order)
    pay(db_conn, UUID(granted.acc["id"]), "6", later + timedelta(minutes=1))
    tick(db_conn, later + timedelta(minutes=2))
    assert renewed(db_conn, first) == (granted.b["id"], D("5.00"), D("1.00"), later.date())


def test_number_and_offering_change_together_at_the_boundary(api, db_conn, granted):
    first, _ = granted.products
    new = number(api, value="79991234009")
    order = switch(api, granted.acc, first, granted.b, first["due"], new["id"])
    assert order["state"] == "completed", order
    rows = db_conn.execute(
        "SELECT n.value, a.effective_from, a.released_on FROM number_assignment a"
        " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
        " WHERE a.product_id = %s ORDER BY a.effective_from",
        (first["id"],),
    ).fetchall()
    assert len(rows) == 2 and rows[1][0] == "79991234009"
    assert rows[0][2] == first["due"] and rows[1][1:] == (first["due"], None)
    early = switch(api, granted.acc, first, granted.a, api.clock.now().date(), new["id"])
    assert early["state"] == "rejected"


def test_postpaid_combined_change_is_rejected(api, db_conn):
    other = apidata.sellable_offering(api, value=5)
    off, acc = shop(api, db_conn, funds="0")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'postpaid' WHERE id = %s", (acc["id"],)
    )
    db_conn.commit()
    item = apidata.add_item(off)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number(api)["id"])}
    added = api.post(ORDER, apidata.order_body(acc, item)).json()
    product = {"id": added["productOrderItem"][0]["product"]["id"]}
    tomorrow = api.clock.now().date() + timedelta(days=1)
    order = switch(api, acc, product, other, tomorrow, number(api, value="79991234008")["id"])
    assert order["state"] == "rejected" and errors(order) == ["combinedChangeUnavailable"]


def test_cancelling_before_the_boundary_drops_the_change(api, db_conn, granted):
    first, _ = granted.products
    order = switch(api, granted.acc, first, granted.b, first["due"])
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)
    pay(db_conn, UUID(granted.acc["id"]), "12", api.clock.now())
    tick(db_conn, at(first["due"], 0))
    assert renewed(db_conn, first)[0] == granted.a["id"]


def test_0044_combined_items_block_downgrade(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    with psycopg.connect(temp_db_url) as conn:
        w = world(conn)
        orders.create_order(
            conn,
            billing_account_id=w.account["id"],
            order_date=datetime(2026, 1, 2, tzinfo=TZ),
            requested_start_date=None,
            items=[
                orders.NewOrderItem(
                    item_id="1",
                    action="modify",
                    product_offering_id=w.offering["id"],
                    product_id=w.product["id"],
                    state="completed",
                    details={"product": {"realizingResource": []}},
                )
            ],
        )
    with pytest.raises(Exception, match="combined offering and number"):
        command.downgrade(cfg, "0043")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("UPDATE product_order_item SET state = 'rejected' WHERE action = 'modify'")
    command.downgrade(cfg, "0043")
    command.upgrade(cfg, "head")
