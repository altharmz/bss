"""Shared builders of tests/test_settlement_db.py, used by other test modules (#423)."""

from datetime import date, timedelta
from decimal import Decimal

from app.billing import settlement
from app.repository import billing, payments
from tests.factories import NOW, days

D = Decimal


def make_bill(conn, w, start: date, amount: str, state: str = "sent", acc=None):
    acc = acc or w.account
    return billing.insert_bill(
        conn,
        billing_account_id=acc["id"],
        currency=acc["currency"],
        bill_period=days(start, start + timedelta(days=28)),
        bill_date=start,
        amount_due=D(amount),
        billing_run_id=w.run["id"],
        payment_due_date=start + timedelta(days=30),
        state=state,
    )


def make_payment(conn, acc, amount: str, minutes: int = 0):
    return payments.insert_payment(
        conn,
        billing_account_id=acc["id"],
        amount=D(amount),
        currency=acc["currency"],
        received_at=NOW + timedelta(minutes=minutes),
        payment_method="cash",
    )


def allocate(conn, acc, bill_id, amount: str, *, payment_id=None, credit_bill_id=None):
    conn.execute(
        """
        INSERT INTO bill_allocation (billing_account_id, currency, customer_bill_id, payment_id,
            credit_bill_id, amount, kind, allocated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'automatic', %s)
        """,
        (acc["id"], acc["currency"], bill_id, payment_id, credit_bill_id, D(amount), NOW),
    )


def settle_account(conn, acc) -> settlement.Settlement:
    core_account = billing.load_account(conn, acc["id"])
    bills, pays = billing.load_settlement(conn, acc["id"])
    result = settlement.settle(bills, pays)
    billing.write_settlement(conn, core_account, result, now=NOW, cause="payment")
    return result
