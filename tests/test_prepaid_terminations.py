"""Prepaid termination credits through orders and the billing owner (P3g)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.billing.model import DateRange
from app.billing.termination import credit
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import loop
from tests import apidata
from tests.apidata import ORDER
from tests.test_api_order_cancel import CANCEL, cancel_body
from tests.test_api_suspension import status_item
from tests.test_events import stored, subscribe
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_posting import TZ
from tests.test_prepaid_renewal_producer import tick
from tests.test_prepaid_status_orders import change, count, errors


@pytest.fixture
def granted(api, db_conn, monkeypatch):
    """Two funded products with numbers, monthly 10 + 20% VAT each, no money left."""
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = shop(api, db_conn)
    order = purchase(api, off, acc)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    rows = db_conn.execute(
        "SELECT product_id, id, lower(period), upper(period) FROM funded_interval"
        " ORDER BY product_id"
    ).fetchall()
    return acc, [{"id": str(r[0]), "grant": r[1], "start": r[2], "due": r[3]} for r in rows]


def terminate(api, acc, *products, **over):
    items = [apidata.delete_item(p["id"], str(i + 1)) for i, p in enumerate(products)]
    response = api.post(ORDER, apidata.order_body(acc, *items, **over))
    assert response.status_code == 201, response.text
    return response.json()


def status(api, product):
    return api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"]


def spendable(conn, acc):
    value = funding.load(conn, UUID(acc["id"])).spendable
    conn.commit()  # load locks the account
    return value


def credits(conn):
    return conn.execute(
        "SELECT a.product_id::text, a.amount, a.tax_amount, lower(a.period), upper(a.period),"
        " b.amount_due, b.state, b.category, b.run_type"
        " FROM applied_customer_billing_rate a JOIN customer_bill b ON b.id = a.customer_bill_id"
        " WHERE a.charge_type = 'refund' ORDER BY a.product_id"
    ).fetchall()


def released(conn, product):
    return conn.execute(
        "SELECT released_on FROM number_assignment WHERE product_id = %s", (product["id"],)
    ).fetchone()[0]


@pytest.mark.parametrize(
    ("ends", "net", "tax"),
    [
        (date(2026, 10, 2), D("-10.00"), D("-2.00")),  # same day: the full period
        (date(2026, 10, 12), D("-6.77"), D("-1.35")),  # 10 of 31 days used: 3.23 net, 0.65 tax
        (date(2026, 11, 1), D("-0.32"), D("-0.06")),  # one day left
        (date(2026, 11, 2), None, None),  # expired: nothing left
    ],
)
def test_credit_rounds_by_difference(ends, net, tax):
    charge = {
        "amount": D("10.00"),
        "applied_tax": [{"taxCategory": "VAT", "taxRate": "20", "taxAmount": "2.00"}],
    }
    found = credit(charge, DateRange(date(2026, 10, 2), date(2026, 11, 2)), ends)
    if net is None:
        assert found is None
    else:
        assert (found[0], [t.amount for t in found[1]]) == (net, [tax])


def test_same_day_termination_credits_the_full_period(api, db_conn, granted):
    acc, (first, second) = granted
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    order = terminate(api, acc, first)
    assert order["state"] == "inProgress" and errors(order) == []
    assert status(api, first) == "terminated"
    assert released(db_conn, first) == first["start"]
    assert credits(db_conn) == [] and spendable(db_conn, acc) == D(0)  # the worker credits

    cmd = commands.get(db_conn, UUID(order["id"]))
    assert (cmd["kind"], cmd["due_date"]) == ("order", first["start"])
    assert tick(db_conn, api.clock.now() + timedelta(minutes=1)).prepaid_commands == [cmd["id"]]
    assert credits(db_conn) == [
        (
            first["id"],
            D("-10.00"),
            D("-2.00"),
            first["start"],
            first["due"],
            D("-12.00"),
            "settled",
            "creditNote",
            "offCycle",
        )
    ]
    assert spendable(db_conn, acc) == D("12.00")
    assert api.get(order["href"]).json()["state"] == "completed"
    assert commands.get(db_conn, cmd["id"])["result"]["outcome"] == "terminated"
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["terminated"]
    assert [e["event"]["productOrder"]["state"] for _, e in stored(db_conn, "TMF622")] == [
        "completed"
    ]
    assert status(api, second) == "active"


def test_mid_interval_termination_credits_unused_dates(api, db_conn, granted):
    acc, (first, _) = granted
    later = datetime.combine(first["start"] + timedelta(days=10), time(10), TZ)
    api.clock.set(later)
    terminate(api, acc, first)
    tick(db_conn, later + timedelta(minutes=1))
    (row,) = credits(db_conn)
    assert row[1:6] == (D("-6.77"), D("-1.35"), later.date(), first["due"], D("-8.12"))
    assert spendable(db_conn, acc) == D("8.12")


def test_future_termination_keeps_service_until_its_date(api, db_conn, granted):
    acc, (first, _) = granted
    ends = first["start"] + timedelta(days=5)
    order = terminate(
        api, acc, first, requestedStartDate=datetime.combine(ends, time(9), TZ).isoformat()
    )
    assert order["state"] == "inProgress"
    assert status(api, first) == "pendingTerminate"
    assert released(db_conn, first) == ends
    assert tick(db_conn, api.clock.now() + timedelta(days=2)).prepaid_commands == []
    assert status(api, first) == "pendingTerminate"  # the observer keeps it

    at = datetime.combine(ends, time(0, 5), TZ)
    assert tick(db_conn, at).prepaid_commands == [UUID(order["id"])]
    (row,) = credits(db_conn)
    assert row[1:5] == (D("-8.39"), D("-1.68"), ends, first["due"])  # 26 of 31 days unused
    stored_status = db_conn.execute("SELECT status FROM product WHERE id = %s", (first["id"],))
    assert stored_status.fetchone() == ("terminated",)
    assert api.post(CANCEL, cancel_body(order)).status_code == 409


def test_expired_interval_gets_no_credit_and_retires_its_renewal(api, db_conn, granted):
    acc, (first, second) = granted
    later = datetime.combine(first["due"] + timedelta(days=2), time(10), TZ)
    tick(db_conn, later)
    renewal = commands.renewal_id(first["grant"])
    assert commands.needs_topup(db_conn, renewal) is not None
    api.clock.set(later)
    order = terminate(api, acc, first, second)
    assert order["state"] == "inProgress"
    result = tick(db_conn, later + timedelta(minutes=1))
    assert set(result.prepaid_commands) == {
        UUID(order["id"]),
        renewal,
        commands.renewal_id(second["grant"]),
    }
    assert credits(db_conn) == [] and spendable(db_conn, acc) == D(0)
    assert commands.get(db_conn, renewal)["result"] == {
        "outcome": "cancelled",
        "reason": "productEnded",
    }
    assert commands.get(db_conn, UUID(order["id"]))["result"] == {
        "outcome": "terminated",
        "credits": [],
    }
    response = api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Closed"})
    assert response.status_code == 200, response.text


def test_termination_cleans_up_a_suspended_account(api, db_conn, granted):
    acc, (first, _) = granted
    assert api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Suspended"}).status_code == 200
    order = terminate(api, acc, first)
    assert order["state"] == "inProgress"
    tick(db_conn, api.clock.now() + timedelta(minutes=1))
    assert [r[1] for r in credits(db_conn)] == [D("-10.00")]
    assert api.get(order["href"]).json()["state"] == "completed"


def test_unsupported_terminations_are_rejected(api, db_conn, granted):
    acc, (first, second) = granted
    mixed = api.post(
        ORDER,
        apidata.order_body(
            acc, apidata.delete_item(first["id"]), status_item(second, "suspended", "2")
        ),
    ).json()
    assert mixed["state"] == "rejected" and errors(mixed) == ["prepaidLifecycleUnavailable"]
    suspended = change(api, acc, first, "suspended")
    assert suspended["state"] == "completed"
    assert terminate(api, acc, first)["state"] == "inProgress"  # a suspended product ends too
    again = terminate(api, acc, first)
    assert again["state"] == "rejected" and errors(again) == ["productAlreadyEnded"]
    assert count(db_conn, "prepaid_command") == 2  # the purchase and one termination


def test_termination_before_first_grant_is_rejected(api, db_conn):
    off, acc = shop(api, db_conn, numbers=False)
    purchase(api, off, acc, numbers=False)
    product = {"id": str(db_conn.execute("SELECT id FROM product LIMIT 1").fetchone()[0])}
    order = terminate(api, acc, product)
    assert order["state"] == "rejected" and errors(order) == ["productNotStarted"]
