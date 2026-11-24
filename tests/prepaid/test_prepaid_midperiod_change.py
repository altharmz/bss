"""A prepaid offering change inside the paid interval (issue #151): credit, new grant."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from alembic.config import Config

from alembic import command
from app.billing.core import prorate, tax_of
from app.billing.model import DateRange
from app.notify import events
from app.worker import prepaid
from tests import apidata
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.prepaid.prepaid_offering_changes import (  # noqa: F401 - fixtures
    at,
    granted,
    offering_of,
    switch,
)
from tests.builders.prepaid.prepaid_orders import purchase, shop
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick
from tests.builders.prepaid.prepaid_status_orders import errors
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.resources.api_resource_inventory import number
from tests.builders.tmf.events import stored, subscribe


def held(conn):
    return conn.execute(
        "SELECT coalesce(sum(h.amount), 0) FROM funding_command_hold c"
        " JOIN funding_hold h ON h.id = c.hold_id WHERE h.released_at IS NULL"
    ).fetchone()[0]


def grants(conn, product):
    return conn.execute(
        "SELECT lower(period), upper(period), amount, tax_amount, supersedes_interval_id"
        " FROM funded_interval WHERE product_id = %s"
        " ORDER BY lower(period), authorized_at, supersedes_interval_id NULLS FIRST",
        (product["id"],),
    ).fetchall()


def refunds(conn, product):
    return conn.execute(
        "SELECT amount, tax_amount FROM applied_customer_billing_rate"
        " WHERE product_id = %s AND charge_type = 'refund'",
        (product["id"],),
    ).fetchall()


def test_downgrade_today_credits_the_whole_interval_and_buys_the_new_one(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, second = granted.products
    today = api.clock.now().date()
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())  # plan B: 5 + 20% tax
    order = switch(api, granted.acc, first, granted.b, today)
    assert order["state"] == "inProgress" and errors(order) == []
    assert held(db_conn) == D("6.00")
    tick(db_conn, api.clock.now())
    (old, new) = grants(db_conn, first)
    assert old[:4] == (today, first["due"], D("10.00"), D("2.00"))
    assert new[0] == today and new[2:4] == (D("5.00"), D("1.00")) and new[4] is not None
    assert refunds(db_conn, first) == [(D("-10.00"), D("-2.00"))]
    assert offering_of(api, first) == granted.b["id"]
    assert grants(db_conn, second)[0][1] == second["due"]  # the other product is untouched
    done = api.get(f"{apidata.ORDER}/{order['id']}").json()
    assert done["state"] == "completed"
    assert held(db_conn) == 0
    tick(db_conn, api.clock.now() + timedelta(minutes=1))  # replay posts nothing twice
    assert len(grants(db_conn, first)) == 2 and len(refunds(db_conn, first)) == 1


def test_a_grant_superseded_from_its_first_day_is_never_the_latest(
    api,
    db_conn,
    granted,  # noqa: F811
):
    """Both grants start today; the latest is the superseding one (authorized later), so
    the renewal producer and the plan-expiring detector see it alone (#311: the latest
    grant is found as the one no later grant follows)."""
    first, _ = granted.products
    today = api.clock.now().date()
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    switch(api, granted.acc, first, granted.b, today)
    tick(db_conn, api.clock.now())
    (old_id, old_end), (new_id, new_end) = db_conn.execute(
        "SELECT id, upper(period) FROM funded_interval WHERE product_id = %s"
        " ORDER BY supersedes_interval_id NULLS FIRST",
        (first["id"],),
    ).fetchall()
    for end in (old_end, new_end):  # a window (end - 1 day, end + 2 days] for each end
        keys = {e.key for e in events._plan_expiring(db_conn, end - timedelta(days=1))}
        assert f"plan_expiring:{old_id}" not in keys
        covered = end - timedelta(days=1) < new_end <= end + timedelta(days=2)
        assert (f"plan_expiring:{new_id}" in keys) == covered
    last = max(old_end, new_end)
    due = db_conn.execute(
        prepaid._DUE_RENEWALS, {"today": last, "account": UUID(granted.acc["id"])}
    ).fetchall()
    assert [row[0] for row in due if row[1] == UUID(first["id"])] == [new_id]


def test_future_dated_change_credits_the_unused_dates_to_the_cent(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    today = api.clock.now().date()
    day = today + timedelta(days=10)
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    order = switch(api, granted.acc, first, granted.b, day)
    assert order["state"] == "inProgress"
    tick(db_conn, at(day - timedelta(days=1)))
    assert len(grants(db_conn, first)) == 1  # nothing before the date
    tick(db_conn, at(day, 0))
    old, new = grants(db_conn, first)
    period = DateRange(today, first["due"])
    kept = prorate(D("10.00"), (day - today).days, period)
    assert refunds(db_conn, first)[0] == (kept - D("10.00"), tax_of(kept, D(20)) - D("2.00"))
    assert new[0] == day and new[1] > day
    api.clock.set(at(day))
    assert offering_of(api, first) == granted.b["id"]


def test_shortfall_holds_nothing_and_the_credit_is_not_counted_on(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    pay(db_conn, UUID(granted.acc["id"]), "5", api.clock.now())  # plan B needs 6
    short = switch(api, granted.acc, first, granted.b, api.clock.now().date())
    assert short["state"] == "rejected" and errors(short) == ["insufficientFunds"]
    assert held(db_conn) == 0
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)


def test_one_change_may_wait_per_product(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    day = api.clock.now().date() + timedelta(days=4)
    pay(db_conn, UUID(granted.acc["id"]), "20", api.clock.now())
    assert switch(api, granted.acc, first, granted.b, day)["state"] == "inProgress"
    again = switch(api, granted.acc, first, granted.a, day + timedelta(days=1))
    assert errors(again) == ["offeringChangePending"]


def test_cancelling_before_the_date_releases_the_hold(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    day = api.clock.now().date() + timedelta(days=4)
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    order = switch(api, granted.acc, first, granted.b, day)
    assert held(db_conn) == D("6.00")
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert held(db_conn) == 0
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)
    tick(db_conn, at(day, 0))
    assert len(grants(db_conn, first)) == 1


def test_cancelling_on_the_date_is_refused(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    order = switch(api, granted.acc, first, granted.b, api.clock.now().date())
    assert api.post(CANCEL, cancel_body(order)).status_code == 409
    assert held(db_conn) == D("6.00")


def test_preview_returns_the_credit_for_the_unused_dates(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    today = api.clock.now().date()
    base = f"/internal/v1/billingAccount/{granted.acc['id']}/firstCharge"
    query = f"?productOffering.id={granted.b['id']}&startDate="
    whole = api.get(f"{base}{query}{today}&product.id={first['id']}").json()
    assert whole["credit"]["value"] == 12.0  # 10 + 2 tax, nothing used
    assert whole["required"]["value"] == 6.0
    assert whole["balanceAfter"]["value"] == whole["balance"]["value"] - 6.0 + 12.0
    day = today + timedelta(days=10)
    part = api.get(f"{base}{query}{day}&product.id={first['id']}").json()
    net = prorate(D("10.00"), 10, DateRange(today, first["due"])) - D("10.00")
    assert part["credit"]["value"] < 12.0 and float(-net) < 10.0
    assert part["credit"]["value"] > float(-net)  # tax is included
    at_renewal = api.get(f"{base}{query}{first['due']}&product.id={first['id']}").json()
    assert at_renewal["credit"]["value"] == 0.0
    plain = api.get(f"{base}{query}{today}").json()
    assert "credit" not in plain
    other = api.get(f"{base}{query}{today}&product.id={UUID(int=1)}")
    assert other.status_code == 422


def test_events_are_emitted_once(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    day = api.clock.now().date() + timedelta(days=2)
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    subscribe(api, "TMF637", "ProductAttributeValueChangeEvent")
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")
    order = switch(api, granted.acc, first, granted.b, day)
    assert stored(db_conn, "TMF637") == []  # nothing changes before the date
    tick(db_conn, at(day, 0))
    product_events = [e["event"]["product"]["id"] for _, e in stored(db_conn, "TMF637")]
    order_events = [e["event"]["productOrder"]["id"] for _, e in stored(db_conn, "TMF622")]
    assert product_events == [first["id"]] and order["id"] in order_events
    tick(db_conn, at(day, 1))  # a replay announces nothing again
    assert [e["event"]["product"]["id"] for _, e in stored(db_conn, "TMF637")] == product_events
    assert [e["event"]["productOrder"]["id"] for _, e in stored(db_conn, "TMF622")] == order_events


def test_number_moves_with_the_offering_on_the_date(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    day = api.clock.now().date() + timedelta(days=2)
    new = number(api, value="79991234010")
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    order = switch(api, granted.acc, first, granted.b, day, new["id"])
    assert order["state"] == "inProgress", errors(order)
    tick(db_conn, at(day, 0))
    rows = db_conn.execute(
        "SELECT n.value, a.effective_from FROM number_assignment a"
        " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
        " WHERE a.product_id = %s ORDER BY a.effective_from",
        (first["id"],),
    ).fetchall()
    assert rows[-1] == ("79991234010", day)
    assert api.get(f"{apidata.ORDER}/{order['id']}").json()["state"] == "completed"


def test_a_product_without_a_grant_cannot_change(api, db_conn):
    other = apidata.sellable_offering(api, value=5)
    off, acc = shop(api, db_conn, numbers=False)
    purchase(api, off, acc, numbers=False)
    product = {"id": str(db_conn.execute("SELECT id FROM product LIMIT 1").fetchone()[0])}
    order = switch(api, acc, product, other, api.clock.now().date())
    assert order["state"] == "rejected" and errors(order) == ["productNotStarted"]


def test_an_ended_product_cannot_change(
    api,
    db_conn,
    granted,  # noqa: F811
):
    first, _ = granted.products
    pay(db_conn, UUID(granted.acc["id"]), "6", api.clock.now())
    terminate(api, granted.acc, first)
    order = switch(api, granted.acc, first, granted.b, api.clock.now().date())
    assert order["state"] == "rejected" and "productAlreadyEnded" in errors(order)


@pytest.mark.migration_history
def test_0050_round_trips(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0049")
    command.upgrade(cfg, "head")
