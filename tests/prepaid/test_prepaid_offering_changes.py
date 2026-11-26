"""Prepaid offering changes take effect only through a funded renewal (P3h)."""
# ruff: noqa: F811

from datetime import datetime, timedelta
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest
from alembic.config import Config

from alembic import command
from app.repository import orders
from app.repository import prepaid_commands as commands
from tests import apidata
from tests.apidata import ORDER
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.prepaid.prepaid_offering_changes import (  # noqa: F401 - fixtures
    applied,
    at,
    granted,
    offering_of,
    switch,
)
from tests.builders.prepaid.prepaid_orders import shop
from tests.builders.prepaid.prepaid_posting import TZ
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick
from tests.builders.prepaid.prepaid_status_orders import errors
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs
from tests.builders.tmf.events import stored, subscribe
from tests.factories import world


def renewed(conn, product):
    return conn.execute(
        "SELECT quote->>'offering_id', amount, tax_amount, lower(period) FROM funded_interval"
        " WHERE product_id = %s AND command_kind = 'renewal'",
        (product["id"],),
    ).fetchone()


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


@pytest.mark.migration_history
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


def test_an_expired_product_changes_its_offering_and_number_today_only(api, db_conn, granted):
    first, _ = granted.products
    later = at(first["due"] + timedelta(days=2))
    tick(db_conn, later)
    api.clock.set(later)
    tomorrow = switch(api, granted.acc, first, granted.b, later.date() + timedelta(days=1))
    assert errors(tomorrow) == ["offeringChangeNotAtRenewal"]
    assert "changes today" in tomorrow["productOrderErrorMessage"][0]["reason"]
    new = number(api, value="79991234011")
    subscribe(api, "TMF637", "ProductAttributeValueChangeEvent")
    order = switch(api, granted.acc, first, granted.b, later.date(), new["id"])
    assert order["state"] == "completed", errors(order)
    shown = api.get(f"{apidata.PRODUCT}/{first['id']}").json()
    assert shown["productOffering"]["id"] == granted.b["id"]
    assert [r["id"] for r in shown["realizingResource"]] == [new["id"]]
    assert [e["event"]["product"]["id"] for _, e in stored(db_conn, "TMF637")] == [first["id"]]


def test_a_prepaid_product_that_is_ending_keeps_its_offering(api, db_conn, granted):
    first, _ = granted.products
    ends = first["due"] - timedelta(days=1)
    ending = terminate(api, granted.acc, first, requestedStartDate=at(ends).isoformat())
    assert ending["state"] == "inProgress", errors(ending)
    order = switch(api, granted.acc, first, granted.b, api.clock.now().date())
    assert order["state"] == "rejected"
    assert [(e["code"], e["reason"]) for e in order["productOrderErrorMessage"]] == [
        ("productAlreadyEnded", f"the prepaid product ends on {ends}")
    ]
    assert offering_of(api, first) == granted.a["id"]
