"""Shared builders of tests/test_funding_db.py, used by other test modules (#423)."""

from decimal import Decimal

from app.repository import billing
from tests.builders.settlement_db import make_bill
from tests.factories import NOW, charge

D = Decimal


def billed_charge(conn, w, start, amount, state="sent"):
    bill = make_bill(conn, w, start, amount, state=state)
    if D(amount) < 0:
        original = conn.execute(
            "SELECT id, period FROM applied_customer_billing_rate"
            " WHERE product_id = %s AND charge_type = 'recurring' ORDER BY period LIMIT 1",
            (w.product["id"],),
        ).fetchone()
        abr = billing.insert_abr(
            conn,
            product_id=w.product["id"],
            billing_account_id=w.account["id"],
            charge_type="refund",
            amount=D(amount),
            currency="USD",
            period=original[1],
            billing_run_id=w.run["id"],
            applied_at=NOW,
            refunds_rate_id=original[0],
        )
    else:
        abr = charge(conn, w, bill["bill_period"], amount)
    billing.attach_abrs_to_bill(conn, bill["id"], w.account["id"], [abr["id"]])
    return bill
