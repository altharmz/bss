"""Prepaid line lifecycle (issue #188): grace, suspension, deactivation and number aging,
driven by the worker on the business date through internal orders."""
# ruff: noqa: F811

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.billing import lifecycle as rules
from app.integrations.network import adapter as network
from app.integrations.payment import adapter as gateway
from tests import apidata, timeline
from tests.apidata import ORDER
from tests.builders.billing.settlement_db import make_payment
from tests.builders.orders.api_suspension import status_item
from tests.builders.prepaid.payment_gateway import commands_of as card_commands
from tests.builders.prepaid.payment_gateway import rule, save
from tests.builders.prepaid.prepaid_lifecycle import (  # noqa: F401 - fixtures
    at,
    go,
    numbered,
    top_up,
)
from tests.builders.prepaid.prepaid_plans import balance, build, buy, offering, price  # noqa: F401
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick
from tests.builders.resources.network_provisioning import make_line, stub  # noqa: F401 - fixtures

pytestmark = pytest.mark.capability("worker.prepaidLifecycle")

ACCOUNTS = "/internal/v1/billingAccount"
SETTINGS = "/internal/v1/lifecycle/settings"
RESOURCE = "/tmf-api/resourceInventory/v5/resource"


def kinds():
    return [kind for kind, _ in network().received.values()]


def shown(api, acc, product_id):
    products = api.get(f"{ACCOUNTS}/{acc['id']}/products").json()
    return next(p for p in products if p["id"] == str(product_id))


def row(db_conn, product_id):
    db_conn.commit()
    return db_conn.execute(
        "SELECT status, manually_suspended, lifecycle_suspended, termination_date"
        " FROM product WHERE id = %s",
        (product_id,),
    ).fetchone()


# --- the dates ---


def test_the_schedule_is_derived_from_the_unfunded_day_and_the_settings():
    settings = rules.Settings(3, 30, 90, 7)
    day = date(2026, 11, 10)
    due = rules.schedule(day, settings)
    assert (due.suspend_on, due.deactivate_on) == (date(2026, 11, 13), date(2026, 12, 13))
    assert rules.state(None, False, day, settings) == "active"
    assert rules.state(day, False, day - timedelta(days=1), settings) == "active"
    assert rules.state(day, False, day, settings) == "grace"
    assert rules.state(day, True, due.suspend_on, settings) == "suspended"
    assert rules.next_transition(day, False, settings) == ("suspend", due.suspend_on)
    assert rules.next_transition(day, True, settings) == ("deactivate", due.deactivate_on)
    assert rules.next_transition(None, False, settings) is None
    assert rules.payg_unfunded_since(day, settings) == date(2026, 11, 17)
    assert rules.payg_unfunded_since(None, settings) is None


# --- unfunded renewal: grace, suspension, deactivation ---


def test_an_unfunded_renewal_follows_grace_suspension_and_deactivation_on_the_dates(
    api, db_conn, numbered
):
    due, product = numbered.due, numbered.product
    go(api, db_conn, due - timedelta(days=1))
    assert shown(api, numbered.acc, product)["lifecycle"] == {
        "state": "active",
        "nextTransition": None,
    }
    # the plan expired unpaid: grace, the service stays on (nothing is sent)
    go(api, db_conn, due)
    view = shown(api, numbered.acc, product)
    assert view["lifecycle"] == {
        "state": "grace",
        "nextTransition": {"action": "suspend", "date": (due + timedelta(days=3)).isoformat()},
    }
    assert view["suspensionCause"] == "funding" and kinds() == []
    go(api, db_conn, due + timedelta(days=2))
    assert kinds() == [] and row(db_conn, product)[2] is False
    # day 3: the line is suspended on the network, through an order
    go(api, db_conn, due + timedelta(days=3))
    assert kinds() == ["suspend"]
    assert row(db_conn, product)[:3] == ("suspended", True, True)
    view = shown(api, numbered.acc, product)
    assert view["suspensionCause"] == "lifecycle"
    assert view["lifecycle"] == {
        "state": "suspended",
        "nextTransition": {"action": "deactivate", "date": (due + timedelta(days=33)).isoformat()},
    }
    go(api, db_conn, due + timedelta(days=32))
    assert kinds() == ["suspend"]
    # day 33: deactivated, the product ends
    go(api, db_conn, due + timedelta(days=33))
    assert kinds() == ["suspend", "deactivate"]
    status, _, _, ended = row(db_conn, product)
    assert (status, ended) == ("terminated", due + timedelta(days=33))
    events = db_conn.execute(
        "SELECT action, business_date, outcome FROM prepaid_lifecycle_event ORDER BY created_at"
    ).fetchall()
    assert events == [
        ("suspend", due + timedelta(days=3), "placed"),
        ("deactivate", due + timedelta(days=33), "placed"),
    ]
    # the orders are internal TMF622 orders on the product's account
    states = db_conn.execute(
        "SELECT o.state FROM prepaid_lifecycle_event e JOIN product_order o"
        " ON o.id = e.product_order_id ORDER BY e.created_at"
    ).fetchall()
    assert states == [("completed",), ("completed",)]


def test_a_renewal_from_the_balance_never_enters_the_lifecycle(api, db_conn, numbered):
    pay(db_conn, UUID(numbered.acc["id"]), "12", api.clock.now())
    go(api, db_conn, numbered.due)
    assert row(db_conn, numbered.product)[0] == "active"
    assert shown(api, numbered.acc, numbered.product)["lifecycle"]["state"] == "active"
    assert db_conn.execute("SELECT count(*) FROM prepaid_lifecycle_event").fetchone() == (0,)


# --- a top-up brings the line back ---


def test_a_top_up_during_grace_renews_and_sends_nothing(api, db_conn, numbered):
    go(api, db_conn, numbered.due + timedelta(days=1))
    assert shown(api, numbered.acc, numbered.product)["lifecycle"]["state"] == "grace"
    top_up(api, db_conn, numbered.acc, "12", numbered.due + timedelta(days=1), ticks=2)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert row(db_conn, numbered.product)[0] == "active"
    view = shown(api, numbered.acc, numbered.product)
    assert view["lifecycle"] == {"state": "active", "nextTransition": None}
    assert kinds() == []
    go(api, db_conn, numbered.due + timedelta(days=5))  # the suspension never comes
    assert kinds() == [] and row(db_conn, numbered.product)[2] is False


def test_a_top_up_during_suspension_renews_and_resumes_the_line(api, db_conn, numbered):
    go(api, db_conn, numbered.due + timedelta(days=3))
    assert kinds() == ["suspend"] and row(db_conn, numbered.product)[2] is True
    # the renewal stayed open (a lifecycle suspension does not block it)
    assert db_conn.execute(
        "SELECT count(*) FROM prepaid_command WHERE kind = 'renewal' AND state = 'pending'"
    ).fetchone() == (1,)
    top_up(api, db_conn, numbered.acc, "12", numbered.due + timedelta(days=5), ticks=4)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert kinds() == ["suspend", "restore"]
    assert row(db_conn, numbered.product)[:3] == ("active", False, False)
    view = shown(api, numbered.acc, numbered.product)
    assert view["lifecycle"] == {"state": "active", "nextTransition": None}
    assert view["suspensionCause"] is None
    # paid for a month from today: nothing more happens until it ends again
    go(api, db_conn, numbered.due + timedelta(days=6))
    assert kinds() == ["suspend", "restore"]


def test_a_top_up_on_the_day_of_the_suspension_resumes_the_next_business_date(
    api, db_conn, numbered
):
    day = numbered.due + timedelta(days=3)
    go(api, db_conn, day)
    assert kinds() == ["suspend"]
    top_up(api, db_conn, numbered.acc, "12", day, ticks=4)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert kinds() == ["suspend"]  # one status change per product and day
    go(api, db_conn, day + timedelta(days=1))
    assert kinds() == ["suspend", "restore"]
    assert row(db_conn, numbered.product)[:3] == ("active", False, False)


def test_a_card_payment_during_suspension_renews_and_resumes_the_line(api, db_conn, build):
    world = build("12", fee=False)  # the month (12) leaves nothing
    network().reset()
    gateway().reset()
    bought = buy(api, db_conn, world)
    suspended_at = bought.due + timedelta(days=3)
    go(api, db_conn, suspended_at)
    assert row(db_conn, UUID(bought.product["id"]))[2] is True
    save(api, world.acc)  # a card is saved and auto-pay is switched on only now
    assert rule(api, world.acc, threshold=1, amount=50).status_code == 200
    day = suspended_at + timedelta(days=1)
    for minute in range(8):  # the gateway answers on a later wake-up than the charge
        api.clock.set(at(day) + timedelta(minutes=minute))
        tick(db_conn, api.clock.now())
    assert ("renewal", "confirmed") in card_commands(db_conn)
    assert row(db_conn, UUID(bought.product["id"]))[:3] == ("active", False, False)
    assert [
        e[0]
        for e in db_conn.execute(
            "SELECT action FROM prepaid_lifecycle_event ORDER BY created_at"
        ).fetchall()
    ] == ["suspend", "resume"]


# --- deactivation ends the product and ages the number ---


def test_deactivation_ends_the_product_and_ages_the_number(api, db_conn, numbered):
    resource = api.get(f"{RESOURCE}?value=12125550177").json()[0]
    end = numbered.due + timedelta(days=33)
    go(api, db_conn, end)
    assert row(db_conn, numbered.product)[0] == "terminated"
    assert db_conn.execute(
        "SELECT aging_days FROM number_assignment WHERE released_on IS NOT NULL"
        " AND resource_specification_id = 'msisdn'"
    ).fetchone() == (90,)

    def status(day):
        api.clock.set(at(day))
        return api.get(f"{RESOURCE}/{resource['id']}").json()["allocationStatus"]

    def claim(day):
        item = apidata.add_item(numbered.off, "1")
        item["product"] = {
            "@type": "ProductRefOrValue",
            "realizingResource": [{"id": resource["id"], "@type": "ResourceRef"}],
        }
        make_payment(db_conn, dict(numbered.acc, currency="USD"), "50")
        db_conn.commit()
        api.clock.set(at(day))
        return api.post(ORDER, apidata.order_body(numbered.acc, item)).json()

    assert status(end) == "quarantined"
    assert status(end + timedelta(days=89)) == "quarantined"
    assert status(end + timedelta(days=90)) == "available"
    early = claim(end + timedelta(days=89))
    assert early["state"] == "rejected"
    assert [e["code"] for e in early["productOrderErrorMessage"]] == ["numberUnavailable"]
    assert claim(end + timedelta(days=90))["state"] == "inProgress"


# --- settings ---


def test_the_defaults_are_three_days_of_grace_thirty_of_suspension_ninety_of_aging(api):
    body = api.get(SETTINGS).json()
    assert body == {
        "graceDays": 3,
        "suspensionDays": 30,
        "numberAgingDays": 90,
        "paygZeroBalanceDays": 7,
        "updatedAt": None,
    }


def test_the_settings_are_changed_and_validated(api):
    changed = api.put(SETTINGS, {"graceDays": 5, "numberAgingDays": 10})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["graceDays"], body["suspensionDays"], body["numberAgingDays"]) == (5, 30, 10)
    assert body["updatedAt"] is not None
    assert api.get(SETTINGS).json() == body
    for bad in (
        {},
        {"graceDays": -1},
        {"graceDays": 366},
        {"suspensionDays": "3"},
        {"graceDays": 1.5},
        {"unknown": 1},
        {"numberAgingDays": 3651},
    ):
        response = api.put(SETTINGS, bad)
        assert response.status_code in (400, 422), (bad, response.text)
    assert api.get(SETTINGS).json() == body


def test_a_settings_change_applies_to_the_transitions_not_yet_taken(api, db_conn, numbered):
    due, product = numbered.due, numbered.product
    go(api, db_conn, due + timedelta(days=1))
    assert shown(api, numbered.acc, product)["lifecycle"]["nextTransition"]["date"] == (
        (due + timedelta(days=3)).isoformat()
    )
    # a shorter grace moves the suspension to the next day
    assert api.put(SETTINGS, {"graceDays": 2}).status_code == 200
    assert shown(api, numbered.acc, product)["lifecycle"]["nextTransition"]["date"] == (
        (due + timedelta(days=2)).isoformat()
    )
    go(api, db_conn, due + timedelta(days=2))
    assert kinds() == ["suspend"]
    # a longer suspension moves the deactivation; the suspension already taken stands
    assert api.put(SETTINGS, {"suspensionDays": 40}).status_code == 200
    assert shown(api, numbered.acc, product)["lifecycle"]["nextTransition"]["date"] == (
        (due + timedelta(days=42)).isoformat()
    )
    go(api, db_conn, due + timedelta(days=6))
    assert kinds() == ["suspend"]
    # a shorter one brings it forward
    assert api.put(SETTINGS, {"suspensionDays": 5, "numberAgingDays": 7}).status_code == 200
    go(api, db_conn, due + timedelta(days=7))
    assert kinds() == ["suspend", "deactivate"]
    # the aging in force when the line was deactivated is the one the number gets
    assert api.put(SETTINGS, {"numberAgingDays": 365}).status_code == 200
    assert db_conn.execute(
        "SELECT aging_days FROM number_assignment WHERE released_on IS NOT NULL"
        " AND resource_specification_id = 'msisdn'"
    ).fetchone() == (7,)


# --- failures and exclusions ---


def test_a_refused_suspension_is_tried_again_the_next_business_date(api, db_conn, numbered):
    network().refuse_kinds.add("suspend")
    day = numbered.due + timedelta(days=3)
    go(api, db_conn, day, ticks=5)
    assert kinds() == [] and row(db_conn, numbered.product)[2] is False
    # one attempt on the date, however many ticks
    assert db_conn.execute("SELECT count(*) FROM prepaid_lifecycle_event").fetchone() == (1,)
    failed = db_conn.execute(
        "SELECT o.state FROM prepaid_lifecycle_event e JOIN product_order o"
        " ON o.id = e.product_order_id"
    ).fetchone()
    assert failed == ("failed",)
    network().refuse_kinds.clear()
    go(api, db_conn, day + timedelta(days=1))
    assert kinds() == ["suspend"] and row(db_conn, numbered.product)[2] is True
    assert db_conn.execute("SELECT count(*) FROM prepaid_lifecycle_event").fetchone() == (2,)


def test_an_operators_suspension_is_left_alone(api, db_conn, numbered):
    product = {"id": str(numbered.product)}
    placed = api.post(ORDER, apidata.order_body(numbered.acc, status_item(product, "suspended")))
    assert placed.status_code == 201, placed.text
    tick(db_conn, api.clock.now())
    tick(db_conn, api.clock.now())
    assert kinds() == ["suspend"]
    go(api, db_conn, numbered.due + timedelta(days=60))
    assert kinds() == ["suspend"]  # no deactivation, no second suspension
    assert row(db_conn, numbered.product)[:3] == ("suspended", True, False)
    assert db_conn.execute("SELECT count(*) FROM prepaid_lifecycle_event").fetchone() == (0,)
    assert shown(api, numbered.acc, numbered.product)["lifecycle"] is None


# --- pay-as-you-go ---


def test_a_pay_as_you_go_line_with_a_zero_balance_follows_the_same_flow(api, db_conn, build):
    world = build("2.40", fee=False)  # the one-time fee (2 + 20% tax) uses everything
    network().reset()
    placed = apidata.order(api, world.acc, apidata.add_item(world.payg))
    assert placed["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    product = apidata.product_of(api, api.get(placed["href"]).json())
    assert api.get(placed["href"]).json()["state"] == "completed"
    assert balance(db_conn, world.acc) == D("0")
    start = api.clock.now().astimezone(timeline.TZ).date()
    go(api, db_conn, start)
    assert db_conn.execute(
        "SELECT balance_zero_since FROM billing_account WHERE id = %s", (world.acc["id"],)
    ).fetchone() == (start,)
    # the balance has to be zero for 7 days, then grace (3 days), then suspension
    assert shown(api, world.acc, product["id"])["lifecycle"]["state"] == "active"
    go(api, db_conn, start + timedelta(days=6))
    assert db_conn.execute("SELECT count(*) FROM prepaid_lifecycle_event").fetchone() == (0,)
    go(api, db_conn, start + timedelta(days=7))
    view = shown(api, world.acc, product["id"])
    assert view["lifecycle"] == {
        "state": "grace",
        "nextTransition": {"action": "suspend", "date": (start + timedelta(days=10)).isoformat()},
    }
    go(api, db_conn, start + timedelta(days=10))
    assert row(db_conn, product["id"])[2] is True
    # a top-up resumes it, and the zero-balance run ends
    make_payment(db_conn, dict(world.acc, currency="USD"), "5")
    db_conn.commit()
    go(api, db_conn, start + timedelta(days=11), ticks=4)
    assert row(db_conn, product["id"])[:3] == ("active", False, False)
    assert db_conn.execute(
        "SELECT balance_zero_since FROM billing_account WHERE id = %s", (world.acc["id"],)
    ).fetchone() == (None,)
