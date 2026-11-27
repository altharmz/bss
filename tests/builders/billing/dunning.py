"""Builders other test modules share, from tests/billing/test_dunning.py (#511)."""

import json
from datetime import date, time, timedelta

from app.worker import loop
from app.worker.dunning import process_dunning
from app.worker.notify import process_notifications
from tests import apidata
from tests.builders.billing.billing_completion import run
from tests.builders.billing.billing_db import add, at
from tests.timeline import NOW, TZ

DEC1 = date(2026, 12, 1)
DUE = date(2026, 12, 31)  # the bill's payment due date (30 days)
OVERDUE = DUE + timedelta(days=1)  # the first overdue date
SETTINGS = "/internal/v1/dunning/settings"
LIST = "/internal/v1/dunning/accounts"
PAYMENT = "/tmf-api/payment/v4/payment"
BILL = "/tmf-api/customerBillManagement/v5/customerBill"


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
