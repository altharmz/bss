"""Prepaid manual suspend/resume orders and account-state rules (P3f)."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import pytest
from psycopg import sql

from app.repository import prepaid_commands as commands
from app.worker import loop
from tests import apidata
from tests.apidata import ORDER
from tests.test_api_order_cancel import CANCEL, cancel_body
from tests.test_api_suspension import status_item
from tests.test_events import stored, subscribe
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_posting import TZ
from tests.test_prepaid_renewal_producer import pay, tick


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


def test_suspend_keeps_paid_interval_and_blocks_renewal(api, db_conn, granted):
    acc, (first, second) = granted
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    charges = count(db_conn, "applied_customer_billing_rate")
    order = change(api, acc, first, "suspended")
    assert order["state"] == "completed" and errors(order) == []
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("suspended", "manual")
    assert shown["paidThrough"] == first["due"].isoformat()
    assert count(db_conn, "funded_interval") == 2
    assert count(db_conn, "applied_customer_billing_rate") == charges
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["suspended"]

    pay(db_conn, UUID(acc["id"]), "24", api.clock.now())
    at_due = datetime.combine(first["due"], time(0, 5), TZ)
    result = tick(db_conn, at_due)
    assert result.prepaid_renewals == [commands.renewal_id(second["grant"])]
    assert count(db_conn, "funded_interval") == 3
    status = dict(db_conn.execute("SELECT id::text, status FROM product").fetchall())
    assert status == {first["id"]: "suspended", second["id"]: "active"}


def test_resume_inside_the_paid_interval_reuses_the_grant(api, db_conn, granted):
    acc, (first, _) = granted
    change(api, acc, first, "suspended")
    api.clock.set(api.clock.now() + timedelta(days=1))
    order = change(api, acc, first, "active")
    assert order["state"] == "completed"
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("active", None)
    assert count(db_conn, "prepaid_command") == 1  # the purchase only
    assert count(db_conn, "funded_interval") == 2


def test_resume_after_expiry_queues_authorization_from_today(api, db_conn, granted):
    acc, (first, second) = granted
    change(api, acc, first, "suspended")
    change(api, acc, second, "suspended")
    later = datetime.combine(first["due"] + timedelta(days=3), time(10), TZ)
    assert tick(db_conn, later).prepaid_renewals == []
    pay(db_conn, UUID(acc["id"]), "12", later)
    api.clock.set(later)
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    assert change(api, acc, first, "active")["state"] == "completed"
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("suspended", "funding")
    renewal = commands.get(db_conn, commands.renewal_id(first["grant"]))
    assert (renewal["kind"], renewal["due_date"], renewal["state"]) == (
        "renewal",
        first["due"],
        "pending",
    )
    assert stored(db_conn, "TMF637") == []  # suspended before and after

    result = tick(db_conn, later + timedelta(minutes=1))
    assert result.prepaid_commands == [renewal["id"]]
    assert db_conn.execute(
        "SELECT lower(period) FROM funded_interval WHERE command_id = %s", (renewal["id"],)
    ).fetchone() == (later.date(),)
    status = dict(db_conn.execute("SELECT id::text, status FROM product").fetchall())
    assert status == {first["id"]: "active", second["id"]: "suspended"}
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["active"]


def test_suspended_account_only_suspends(api, db_conn, granted):
    acc, (first, second) = granted
    change(api, acc, first, "suspended")
    assert api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Suspended"}).status_code == 200
    api.clock.set(api.clock.now() + timedelta(days=1))
    resumed = change(api, acc, first, "active")
    assert resumed["state"] == "rejected" and errors(resumed) == ["accountNotActive"]
    assert change(api, acc, second, "suspended")["state"] == "completed"
    assert view(api, acc, second)["suspensionCause"] == "manual"


def test_unsupported_prepaid_status_orders_are_rejected(api, db_conn, granted):
    acc, (first, second) = granted
    tomorrow = (api.clock.now() + timedelta(days=1)).isoformat()
    future = change(api, acc, first, "suspended", requestedStartDate=tomorrow)
    assert errors(future) == ["prepaidLifecycleUnavailable"]
    mixed = api.post(
        ORDER,
        apidata.order_body(
            acc, status_item(first, "suspended"), apidata.delete_item(second["id"], "2")
        ),
    ).json()
    assert mixed["state"] == "rejected" and errors(mixed) == ["prepaidLifecycleUnavailable"]
    assert view(api, acc, first)["status"] == "active"


def test_status_change_before_first_grant_is_rejected(api, db_conn):
    off, acc = shop(api, db_conn, numbers=False)
    purchase(api, off, acc, numbers=False)
    product = {"id": str(db_conn.execute("SELECT id FROM product LIMIT 1").fetchone()[0])}
    order = change(api, acc, product, "suspended")
    assert order["state"] == "rejected" and errors(order) == ["productNotStarted"]


def test_cancelling_a_status_order_on_its_day_restores_the_cause(api, db_conn, granted):
    acc, (first, _) = granted
    order = change(api, acc, first, "suspended")
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("active", None)
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["active"]

    later = datetime.combine(first["due"] + timedelta(days=1), time(10), TZ)
    suspended = change(api, acc, first, "suspended")
    api.clock.set(later)
    resumed = change(api, acc, first, "active")
    renewal = commands.renewal_id(first["grant"])
    assert commands.get(db_conn, renewal) is not None
    assert api.post(CANCEL, cancel_body(resumed)).status_code == 201
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("suspended", "manual")
    pay(db_conn, UUID(acc["id"]), "24", later)
    assert renewal not in tick(db_conn, later + timedelta(minutes=1)).prepaid_commands
    assert commands.get(db_conn, renewal)["attempts"] == 0
    assert api.post(CANCEL, cancel_body(suspended)).status_code == 409  # took effect earlier


def test_close_refused_with_live_products_held_money_or_commands(api, db_conn, granted):
    acc, _ = granted
    href = f"{apidata.ACCOUNT}/{acc['id']}"
    response = api.patch(href, {"state": "Closed"})
    assert response.status_code == 409
    assert "2 products not yet ended" in response.text
    assert api.get(href).json()["state"] == "Active"


def test_close_refused_with_held_money_and_pending_command(api, db_conn):
    off, acc = shop(api, db_conn, numbers=False)
    purchase(api, off, acc, numbers=False)
    response = api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Closed"})
    assert response.status_code == 409
    assert "held funds" in response.text and "unfinished prepaid commands" in response.text


def test_close_allowed_once_everything_ended_and_credit_kept(api, db_conn):
    off, acc = shop(api, db_conn, numbers=False, funds="30")
    body = apidata.order_body(
        acc,
        apidata.add_item(off),
        requestedStartDate=(api.clock.now() + timedelta(days=1)).isoformat(),
    )
    order = api.post(ORDER, body).json()
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    response = api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Closed"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "Closed"
    balance = response.json()["accountBalance"][0]["amount"]["value"]
    assert D(str(balance)) == D(30)
