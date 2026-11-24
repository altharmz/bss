"""Postpaid dunning (#385, docs/decisions/dunning.md): an overdue postpaid bill brings reminders,
then the suspension of the account's products through internal orders, then their restoration
once it is paid - each step once, on the configured days after O (the first overdue date)."""
# ruff: noqa: F811

import json
from datetime import date, time, timedelta
from decimal import Decimal as D

import pytest

from app.billing import dunning as rules
from app.integrations.notify import adapter as sender
from app.worker import loop
from app.worker.dunning import process_dunning
from app.worker.notify import process_notifications
from tests import apidata
from tests.apiclient import decimals
from tests.builders.billing.billing_completion import run, worker  # noqa: F401 - fixtures
from tests.builders.billing.billing_db import add, at
from tests.builders.orders.api_suspension import status_item
from tests.timeline import NOW, TZ

pytestmark = pytest.mark.capability("worker.postpaidDunning")

DEC1 = date(2026, 12, 1)
DUE = date(2026, 12, 31)  # the bill's payment due date (30 days)
OVERDUE = DUE + timedelta(days=1)  # the first overdue date
SETTINGS = "/internal/v1/dunning/settings"
LIST = "/internal/v1/dunning/accounts"
PAYMENT = "/tmf-api/payment/v4/payment"
BILL = "/tmf-api/customerBillManagement/v5/customerBill"


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


def billed(api, worker, db_conn, *values):
    """Postpaid accounts with one product each from Dec 1 (310 by default), their first bills
    issued by one run (due Dec 31), each with an email and a phone to tell. One account: the
    (account, product) pair; more: a list of them."""
    api.clock.set(NOW)
    made = []
    for value in values or (310,):
        offering = apidata.sellable_offering(api, value=value)
        account = apidata.account(api)
        made.append((account, add(api, account, offering, DEC1, on=at(date(2026, 11, 20)))))
        api.clock.set(NOW)
        db_conn.execute(
            "UPDATE customer SET details = details || %s::jsonb"
            " WHERE id = (SELECT customer_id FROM billing_account WHERE id = %s)",
            (
                json.dumps(
                    {
                        "contactMedium": [
                            {"@type": "EmailContactMedium", "emailAddress": "olga@example.org"},
                            {"@type": "PhoneContactMedium", "phoneNumber": "12125550100"},
                        ]
                    }
                ),
                account["id"],
            ),
        )
        db_conn.commit()
    run(worker, api, DEC1)
    return made[0] if len(made) == 1 else made


def day(api, worker, when, *, full=True, hour=9):
    """The business clock is `when`; one dunning pass (the full sweep by default)."""
    api.clock.set(at(when, hour))
    return named(process_dunning(worker, clock=api.clock, tz=TZ, full=full))


def named(taken):
    return [(str(account_id), step) for account_id, step in taken]


def tick(api, worker, when):
    api.clock.set(when)
    return named(loop.tick(worker, clock=api.clock, tz=TZ, run_at=time(0, 30)).dunning)


def notify(api, worker):
    """The notification pass, twice: the worker may run it again."""
    process_notifications(worker, clock=api.clock, tz=TZ)
    process_notifications(worker, clock=api.clock, tz=TZ)


def steps(db_conn, account):
    rows = db_conn.execute(
        "SELECT step, business_date, overdue_since, products FROM dunning_step"
        " WHERE billing_account_id = %s ORDER BY taken_at, step",
        (account["id"],),
    ).fetchall()
    db_conn.commit()
    return rows


def product_row(db_conn, product):
    row = db_conn.execute(
        "SELECT status, dunning_suspended FROM product WHERE id = %s", (product["id"],)
    ).fetchone()
    db_conn.commit()
    return row


def pay(api, account, value):
    response = api.post(PAYMENT, apidata.payment_body(account, value=value))
    assert response.status_code == 201, response.text
    return response.json()


def bill_of(api, account):
    [bill] = api.get(f"{BILL}?billingAccount.id={account['id']}").json()
    return bill


def events(db_conn, account):
    rows = db_conn.execute(
        "SELECT event_type, channel FROM notification_message WHERE billing_account_id = %s"
        " AND event_type LIKE 'dunning%%' ORDER BY event_type, channel",
        (account["id"],),
    ).fetchall()
    db_conn.commit()
    return rows


# --- the dates (pure) ---


def test_the_due_step_follows_the_overdue_date_and_the_settings_at_the_boundaries():
    settings = rules.Settings(1, 7, 14, D("5.00"))
    o = date(2027, 1, 1)
    assert rules.overdue_since([date(2026, 12, 31), date(2027, 1, 30)]) == o
    assert rules.overdue_since([]) is None
    assert rules.schedule(o, settings) == {
        "reminder1": date(2027, 1, 2),
        "reminder2": date(2027, 1, 8),
        "suspend": date(2027, 1, 15),
    }
    assert rules.due_step(o, settings, date(2027, 1, 1), []) is None
    assert rules.due_step(o, settings, date(2027, 1, 2), []) == "reminder1"
    assert rules.due_step(o, settings, date(2027, 1, 7), ["reminder1"]) is None
    assert rules.due_step(o, settings, date(2027, 1, 8), ["reminder1"]) == "reminder2"
    assert rules.due_step(o, settings, date(2027, 1, 14), ["reminder1", "reminder2"]) is None
    assert rules.due_step(o, settings, date(2027, 1, 15), ["reminder1", "reminder2"]) == "suspend"
    assert rules.due_step(o, settings, date(2027, 3, 1), ["reminder1", "reminder2", "suspend"]) is (
        None
    )
    # a worker that was down takes only the latest step, never an earlier one afterwards
    assert rules.due_step(o, settings, date(2027, 1, 20), []) == "suspend"
    assert rules.due_step(o, settings, date(2027, 1, 20), ["suspend"]) is None
    # a settings change that moves the suspension later never brings back a skipped reminder
    later = rules.Settings(1, 7, 30, D("5.00"))
    assert rules.due_step(o, later, date(2027, 1, 20), ["reminder2"]) is None
    assert rules.next_step(o, settings, date(2027, 1, 1), []) == ("reminder1", date(2027, 1, 2))
    assert rules.next_step(o, settings, date(2027, 1, 3), ["reminder1"]) == (
        "reminder2",
        date(2027, 1, 8),
    )
    assert rules.next_step(o, settings, date(2027, 1, 20), []) == ("suspend", date(2027, 1, 15))
    assert rules.next_step(o, settings, date(2027, 1, 20), ["reminder1", "suspend"]) is None
    assert rules.in_dunning(o, D("5.00"), settings) and not rules.in_dunning(o, D("4.99"), settings)
    assert not rules.in_dunning(None, D("100"), settings)
    assert not rules.in_dunning(o, D("0"), rules.Settings(1, 7, 14, D("0")))


# --- the timeline ---


def test_an_overdue_bill_brings_reminders_then_suspension_then_restoration_when_paid(
    api, worker, db_conn, log_sender
):
    account, product = billed(api, worker, db_conn)
    assert day(api, worker, DUE) == [] and day(api, worker, OVERDUE) == []  # due, then overdue
    view = api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()
    assert view["inDunning"] and view["overdueSince"] == OVERDUE.isoformat()
    assert view["overdueAmount"] == {"unit": "USD", "value": 310}
    assert view["nextStep"] == {"step": "reminder1", "date": "2027-01-02"}
    # O + 1: the first reminder, once even with two passes
    assert day(api, worker, OVERDUE + timedelta(days=1)) == [(account["id"], "reminder1")]
    assert day(api, worker, OVERDUE + timedelta(days=1)) == []
    notify(api, worker)
    assert day(api, worker, OVERDUE + timedelta(days=6)) == []
    assert day(api, worker, OVERDUE + timedelta(days=7)) == [(account["id"], "reminder2")]
    assert day(api, worker, OVERDUE + timedelta(days=7)) == []
    notify(api, worker)
    assert product_row(db_conn, product) == ("active", False)
    # O + 14: every active product is suspended through an internal order
    assert day(api, worker, OVERDUE + timedelta(days=14)) == [(account["id"], "suspend")]
    assert day(api, worker, OVERDUE + timedelta(days=14)) == []
    assert product_row(db_conn, product) == ("suspended", True)
    orders = db_conn.execute(
        "SELECT o.state, c.status, c.cause FROM product_status_change c"
        " JOIN product_order_item i ON i.id = c.changed_by_item_id"
        " JOIN product_order o ON o.id = i.product_order_id WHERE c.product_id = %s",
        (product["id"],),
    ).fetchall()
    db_conn.commit()
    assert orders == [("completed", "suspended", "dunning")]
    assert api.get(f"/tmf-api/productInventory/v5/product/{product['id']}").json()["status"] == (
        "suspended"
    )
    view = api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()
    assert [s["step"] for s in view["stepsTaken"]] == ["reminder1", "reminder2", "suspend"]
    assert view["nextStep"] is None
    assert view["suspendedProducts"] == [{"id": product["id"]}]
    # the customer is told each step once, by email and SMS
    notify(api, worker)
    assert events(db_conn, account) == [
        ("dunning_reminder1", "email"),
        ("dunning_reminder1", "sms"),
        ("dunning_reminder2", "email"),
        ("dunning_reminder2", "sms"),
        ("dunning_suspended", "email"),
        ("dunning_suspended", "sms"),
    ]
    # paid the next day: a tick that is no full sweep restores the product
    api.clock.set(at(OVERDUE + timedelta(days=15), 8))
    pay(api, account, 310)
    assert bill_of(api, account)["state"] == "settled"
    assert day(api, worker, OVERDUE + timedelta(days=15), full=False) == [
        (account["id"], "restore")
    ]
    assert day(api, worker, OVERDUE + timedelta(days=15), full=False) == []
    assert product_row(db_conn, product) == ("active", False)
    assert [(s[0], s[3]) for s in steps(db_conn, account)] == [
        ("reminder1", 0),
        ("reminder2", 0),
        ("suspend", 1),
        ("restore", 1),
    ]
    view = api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()
    assert view["inDunning"] is False and view["suspendedProducts"] == []


def test_the_worker_tick_runs_the_steps_on_its_full_sweep_and_restores_on_any_tick(
    api, worker, db_conn
):
    account, product = billed(api, worker, db_conn)
    assert tick(api, worker, at(DEC1, 11)) == []
    # a worker that was down for the reminders takes only the suspension, on the date's
    # first tick (the full sweep); later ticks of the date take nothing
    assert tick(api, worker, at(OVERDUE + timedelta(days=20), 9)) == [(account["id"], "suspend")]
    assert tick(api, worker, at(OVERDUE + timedelta(days=20), 10)) == []
    assert tick(api, worker, at(OVERDUE + timedelta(days=21), 9)) == []
    assert product_row(db_conn, product) == ("suspended", True)
    pay(api, account, 310)
    # a payment later in the day: the next tick, no full sweep, restores the product
    assert tick(api, worker, at(OVERDUE + timedelta(days=21), 10)) == [(account["id"], "restore")]
    assert product_row(db_conn, product) == ("active", False)


def test_a_suspension_paid_on_its_own_day_is_restored_the_next_business_date(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    day(api, worker, OVERDUE + timedelta(days=14))
    api.clock.set(at(OVERDUE + timedelta(days=14), 11))
    pay(api, account, 310)
    assert day(api, worker, OVERDUE + timedelta(days=14), full=False, hour=12) == []
    assert product_row(db_conn, product) == ("suspended", True)
    assert day(api, worker, OVERDUE + timedelta(days=15), full=False) == [
        (account["id"], "restore")
    ]
    assert product_row(db_conn, product) == ("active", False)


# --- what never counts ---


def test_a_held_bill_is_never_overdue(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    held = api.request(
        "PATCH",
        f"{BILL}/{bill_of(api, account)['id']}",
        content='{"state": "onHold"}',
        headers={"Content-Type": "application/merge-patch+json"},
    )
    assert held.status_code == 200, held.text
    assert day(api, worker, OVERDUE + timedelta(days=20)) == []
    assert steps(db_conn, account) == [] and product_row(db_conn, product) == ("active", False)
    assert api.get(LIST).json() == []


def test_an_amount_under_the_minimum_triggers_nothing(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    api.clock.set(at(date(2026, 12, 5)))
    pay(api, account, 306)  # 4.00 left, under the 5.00 default
    assert day(api, worker, OVERDUE + timedelta(days=20)) == []
    assert steps(db_conn, account) == []
    assert api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()["inDunning"] is (
        False
    )


def test_a_partial_payment_below_the_minimum_restores_the_lines(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    day(api, worker, OVERDUE + timedelta(days=14))
    api.clock.set(at(OVERDUE + timedelta(days=15), 8))
    pay(api, account, 306)
    assert day(api, worker, OVERDUE + timedelta(days=15), full=False) == [
        (account["id"], "restore")
    ]
    assert product_row(db_conn, product) == ("active", False)


def test_an_exempt_account_takes_no_step_and_its_suspension_is_lifted(api, worker, db_conn):
    (account, product), (other, other_product) = billed(api, worker, db_conn, 310, 310)
    url = f"/internal/v1/billingAccount/{account['id']}/dunning"
    exempted = api.request(
        "PATCH",
        url,
        content='{"exempt": true, "reason": "disputed bill"}',
        headers={"Content-Type": "application/merge-patch+json", "X-Remote-User": "i.petrova"},
    )
    assert exempted.status_code == 200, exempted.text
    assert exempted.json()["exempt"] is True and exempted.json()["inDunning"] is False
    assert day(api, worker, OVERDUE + timedelta(days=14)) == [(other["id"], "suspend")]
    assert product_row(db_conn, product) == ("active", False)
    # exempting a suspended account lifts its suspension on the next pass
    assert (
        api.patch(
            f"/internal/v1/billingAccount/{other['id']}/dunning", {"exempt": True}
        ).status_code
        == 200
    )
    assert day(api, worker, OVERDUE + timedelta(days=15), full=False) == [(other["id"], "restore")]
    assert product_row(db_conn, other_product) == ("active", False)
    # the same value again writes nothing; each change is audited
    assert api.patch(url, {"exempt": True}).status_code == 200
    assert api.patch(url, {"exempt": False}).status_code == 200
    audit = db_conn.execute(
        "SELECT operator, reason, detail->>'exempt' FROM account_audit"
        " WHERE billing_account_id = %s AND action = 'dunning_exemption' ORDER BY seq",
        (account["id"],),
    ).fetchall()
    db_conn.commit()
    assert audit == [("i.petrova", "disputed bill", "true"), ("i.petrova", None, "false")]
    entries = api.get(f"/internal/v1/security/audit?billingAccount.id={account['id']}").json()
    assert {e["action"] for e in entries} == {"dunning_exemption"}
    # an operator's setting is no customer security alert
    process_notifications(worker, clock=api.clock, tz=TZ)
    alerts = db_conn.execute(
        "SELECT count(*) FROM notification_message WHERE event_type LIKE 'security_%%'"
    ).fetchone()
    db_conn.commit()
    assert alerts == (0,)


def test_an_operators_suspension_is_not_lifted_by_restoration(api, worker, db_conn):
    api.clock.set(NOW)
    paused_offering = apidata.sellable_offering(api, value=20, name="Second line")
    account, product = billed(api, worker, db_conn)
    paused = add(api, account, paused_offering, DEC1 + timedelta(days=1), on=at(DEC1, 12))
    api.clock.set(at(OVERDUE + timedelta(days=3)))
    placed = apidata.order(api, account, status_item(paused, "suspended"))
    assert placed["state"] == "completed", placed
    assert day(api, worker, OVERDUE + timedelta(days=14)) == [(account["id"], "suspend")]
    assert [(s[0], s[3]) for s in steps(db_conn, account)][-1] == ("suspend", 1)
    assert product_row(db_conn, product) == ("suspended", True)
    assert product_row(db_conn, paused) == ("suspended", False)
    api.clock.set(at(OVERDUE + timedelta(days=15), 8))
    pay(api, account, 310)
    assert day(api, worker, OVERDUE + timedelta(days=15)) == [(account["id"], "restore")]
    assert product_row(db_conn, product) == ("active", False)
    assert product_row(db_conn, paused) == ("suspended", False)  # the operator's pause stays
    assert [(s[0], s[3]) for s in steps(db_conn, account)][-1] == ("restore", 1)


def test_nothing_suspended_by_dunning_sends_no_suspension_notice(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    api.clock.set(at(OVERDUE + timedelta(days=3)))
    placed = apidata.order(api, account, status_item(product, "suspended"))
    assert placed["state"] == "completed", placed
    assert day(api, worker, OVERDUE + timedelta(days=14)) == [(account["id"], "suspend")]
    assert [(s[0], s[3]) for s in steps(db_conn, account)][-1] == ("suspend", 0)
    notify(api, worker)
    assert ("dunning_suspended", "email") not in events(db_conn, account)
    assert ("dunning_suspended", "sms") not in events(db_conn, account)


def test_an_operators_resumption_during_dunning_clears_the_flag(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    day(api, worker, OVERDUE + timedelta(days=14))
    api.clock.set(at(OVERDUE + timedelta(days=16)))
    placed = apidata.order(api, account, status_item(product, "active"))
    assert placed["state"] == "completed", placed
    assert product_row(db_conn, product) == ("active", False)
    assert day(api, worker, OVERDUE + timedelta(days=17)) == []  # not suspended again
    assert product_row(db_conn, product) == ("active", False)


def test_suspended_and_closed_accounts_are_skipped(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    assert api.patch(account["href"], {"state": "Suspended"}).status_code == 200
    assert day(api, worker, OVERDUE + timedelta(days=14)) == []
    assert steps(db_conn, account) == []


# --- settings ---


def test_a_settings_change_midway_moves_only_the_steps_not_yet_taken(api, worker, db_conn):
    account, _ = billed(api, worker, db_conn)
    assert day(api, worker, OVERDUE + timedelta(days=1)) == [(account["id"], "reminder1")]
    changed = api.patch(SETTINGS, {"reminder2Days": 10, "suspendDays": 20})
    assert changed.status_code == 200, changed.text
    assert day(api, worker, OVERDUE + timedelta(days=7)) == []
    assert day(api, worker, OVERDUE + timedelta(days=10)) == [(account["id"], "reminder2")]
    assert day(api, worker, OVERDUE + timedelta(days=14)) == []
    assert day(api, worker, OVERDUE + timedelta(days=20)) == [(account["id"], "suspend")]
    assert [s[:2] for s in steps(db_conn, account)] == [
        ("reminder1", OVERDUE + timedelta(days=1)),
        ("reminder2", OVERDUE + timedelta(days=10)),
        ("suspend", OVERDUE + timedelta(days=20)),
    ]


def test_the_settings_have_defaults_and_are_validated(api):
    body = api.get(SETTINGS).json()
    assert body == {
        "reminder1Days": 1,
        "reminder2Days": 7,
        "suspendDays": 14,
        "minimumOverdue": 5,
        "updatedAt": None,
    }
    assert api.patch(SETTINGS, {"reminder2Days": 20}).status_code == 422  # not increasing
    assert api.patch(SETTINGS, {"suspendDays": 366}).status_code == 422
    assert api.patch(SETTINGS, {"reminder1Days": -1}).status_code == 422
    assert api.patch(SETTINGS, {"minimumOverdue": None}).status_code == 422
    assert api.patch(SETTINGS, {"minimumOverdue": 1.005}).status_code == 422
    assert api.patch(SETTINGS, {"minimumOverdue": "5"}).status_code == 422
    assert api.patch(SETTINGS, {}).status_code == 400
    assert api.patch(SETTINGS, {"graceDays": 3}).status_code == 400
    changed = api.patch(SETTINGS, {"reminder1Days": 0, "minimumOverdue": 12.5})
    assert changed.status_code == 200, changed.text
    assert decimals(changed)["reminder1Days"] == 0
    assert decimals(changed)["minimumOverdue"] == D("12.50")
    assert changed.json()["updatedAt"] is not None


# --- the views ---


def test_the_dunning_list_shows_the_accounts_with_their_last_step_and_candidates(
    api, worker, db_conn
):
    (first, _), (second, _) = billed(api, worker, db_conn, 310, 40)
    day(api, worker, OVERDUE + timedelta(days=1))
    api.clock.set(at(OVERDUE + timedelta(days=2)))
    rows = api.get(LIST).json()
    assert sorted((r["billingAccount"]["id"], r["lastStep"]) for r in rows) == sorted(
        [(first["id"], "reminder1"), (second["id"], "reminder1")]
    )
    assert {r["overdueAmount"]["value"] for r in rows} == {310, 40}
    assert all(r["overdueSince"] == OVERDUE.isoformat() for r in rows)
    assert api.get(f"{LIST}?step=suspend").json() == []
    assert api.get(f"{LIST}?step=bogus").status_code == 400
    page = api.get(f"{LIST}?limit=1")
    assert len(page.json()) == 1 and 'rel="next"' in page.headers.get("Link", "")
    day(api, worker, OVERDUE + timedelta(days=14))
    api.clock.set(at(OVERDUE + timedelta(days=14 + 60)))
    assert api.get(f"{LIST}?deactivationCandidate=true").json() == []
    api.clock.set(at(OVERDUE + timedelta(days=14 + 61)))
    candidates = api.get(f"{LIST}?deactivationCandidate=true").json()
    assert len(candidates) == 2 and all(r["deactivationCandidate"] for r in candidates)
    assert all(
        r["suspendedSince"] == (OVERDUE + timedelta(days=14)).isoformat() for r in candidates
    )


def test_a_prepaid_account_is_never_in_dunning_and_cannot_be_exempted(api):
    account = apidata.account(api, ratingType="prepaid")
    url = f"/internal/v1/billingAccount/{account['id']}/dunning"
    view = api.get(url).json()
    assert view["inDunning"] is False and view["exempt"] is False
    assert api.patch(url, {"exempt": True}).status_code == 422
    unknown = "/internal/v1/billingAccount/00000000-0000-0000-0000-000000000000/dunning"
    assert api.get(unknown).status_code == 404
