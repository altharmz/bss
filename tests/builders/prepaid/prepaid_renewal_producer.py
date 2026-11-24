"""Builders other test modules share, from tests/prepaid/test_prepaid_renewal_producer.py (#423)."""

from datetime import time
from decimal import Decimal as D

from app.clock import FixedClock
from app.repository import payments
from app.worker import loop
from tests.builders.prepaid.prepaid_posting import TZ


def tick(conn, now):
    result = loop.tick(conn, clock=FixedClock(now), tz=TZ, run_at=time(0, 30))
    conn.commit()
    return result


def pay(conn, account_id, amount, at):
    payments.insert_payment(
        conn,
        billing_account_id=account_id,
        amount=D(amount),
        currency="USD",
        received_at=at,
        payment_method="cash",
    )
    conn.commit()
