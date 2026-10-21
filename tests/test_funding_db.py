"""Shared funding reconciles actual legacy ledger/source history without writes."""

from datetime import date
from decimal import Decimal

import pytest

from app.billing.funding import InsufficientFunds
from app.billing.model import InconsistentState
from app.repository import billing, funding
from tests.factories import NOW, account, charge, days, world
from tests.test_settlement_db import allocate, make_bill, make_payment, settle_account

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


def test_empty_and_other_account_funds(db_conn):
    mine = account(db_conn)
    other = account(db_conn, currency="EUR", name="Other")
    make_payment(db_conn, other, "100")
    snapshot = funding.load(db_conn, mine["id"])
    assert snapshot.currency == "USD"
    assert snapshot.sources == ()
    assert snapshot.ledger == snapshot.spendable == snapshot.unpaid_obligations == 0


def test_reconciles_before_and_after_settlement(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "40")
    billed_charge(db_conn, w, date(2026, 2, 1), "-10", "settled")
    charge(db_conn, w, days(date(2026, 3, 1), date(2026, 4, 1)), "5")
    make_payment(db_conn, w.account, "60")
    db_conn.commit()
    before = funding.load(db_conn, w.account["id"])
    assert before.spendable == before.ledger == D("25")
    assert before.unpaid_obligations == D("45")
    settle_account(db_conn, w.account)
    after = funding.load(db_conn, w.account["id"])
    assert after.spendable == before.spendable
    assert after.unpaid_obligations == D("5")
    assert sum(s.consumed for s in after.sources) == D("40")


@pytest.mark.parametrize("state", ["new", "validated", "onHold", "sent"])
def test_positive_obligations_remain_even_when_not_open(db_conn, state):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "40", state)
    make_payment(db_conn, w.account, "10")
    snapshot = funding.load(db_conn, w.account["id"])
    assert snapshot.unpaid_obligations == D("40")
    assert snapshot.spendable == D("-30")


def test_combines_payment_and_credit_note_payout_consumption(db_conn):
    w = world(db_conn)
    bill = billed_charge(db_conn, w, date(2026, 1, 1), "25")
    note = billed_charge(db_conn, w, date(2026, 2, 1), "-20", "settled")
    pay = make_payment(db_conn, w.account, "30")
    allocate(db_conn, w.account, bill["id"], "25", payment_id=pay["id"])
    refund_id = db_conn.execute(
        "INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_name)"
        " VALUES (%s, 'USD', 12, %s, 'cash') RETURNING id",
        (w.account["id"], NOW),
    ).fetchone()[0]
    for payment_id, note_id, amount in [(pay["id"], None, 5), (None, note["id"], 7)]:
        db_conn.execute(
            "INSERT INTO refund_source"
            " (refund_id, billing_account_id, payment_id, credit_bill_id, amount)"
            " VALUES (%s, %s, %s, %s, %s)",
            (refund_id, w.account["id"], payment_id, note_id, amount),
        )
    db_conn.commit()
    snapshot = funding.load(db_conn, w.account["id"])
    assert snapshot.ledger == snapshot.spendable == D("13")
    assert {s.kind: s.consumed for s in snapshot.sources} == {
        "payment": D("30"),
        "creditNote": D("7"),
    }


def test_mismatched_legacy_bill_is_technical_failure(db_conn):
    w = world(db_conn)
    make_bill(db_conn, w, date(2026, 1, 1), "40")  # No backing ABRs
    make_payment(db_conn, w.account, "60")
    with pytest.raises(InconsistentState, match="reconcile"):
        funding.load(db_conn, w.account["id"])


def test_unissued_credit_is_not_a_funding_source(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "40")
    billed_charge(db_conn, w, date(2026, 2, 1), "-10", "new")
    with pytest.raises(InconsistentState, match="reconcile"):
        funding.load(db_conn, w.account["id"])


def test_plan_limits_unused_sources_by_unpaid_obligations(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "40", "onHold")
    charge(db_conn, w, days(date(2026, 3, 1), date(2026, 4, 1)), "5")
    payment = make_payment(db_conn, w.account, "60")
    result = funding.plan(db_conn, w.account["id"], D("15"))
    assert result.amount == result.snapshot.spendable == D("15")
    assert [(s.kind, s.id, s.amount) for s in result.sources] == [
        ("payment", payment["id"], D("15"))
    ]
    with pytest.raises(InsufficientFunds) as exc:
        funding.plan(db_conn, w.account["id"], D("15.01"))
    assert exc.value.required == D("15.01")
    assert exc.value.available == D("15")
    # Planning is read-only, including repeated quotes in the same transaction.
    assert funding.plan(db_conn, w.account["id"], D("15")) == result


def test_plan_pins_remaining_payment_then_issued_credit(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "25")
    note = billed_charge(db_conn, w, date(2026, 2, 1), "-20", "settled")
    payment = make_payment(db_conn, w.account, "30")
    settle_account(db_conn, w.account)
    result = funding.plan(db_conn, w.account["id"], D("12.34"))
    assert [(s.kind, s.id, s.amount) for s in result.sources] == [
        ("payment", payment["id"], D("5")),
        ("creditNote", note["id"], D("7.34")),
    ]


def test_plan_debt_has_no_available_funds_but_accepts_zero(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "40")
    make_payment(db_conn, w.account, "10")
    assert funding.plan(db_conn, w.account["id"], D(0)).sources == ()
    with pytest.raises(InsufficientFunds) as exc:
        funding.plan(db_conn, w.account["id"], D("0.01"))
    assert exc.value.available == 0


@pytest.mark.parametrize("amount", [D("-1"), D("NaN"), D("Infinity"), 1, 1.0])
def test_plan_rejects_invalid_amount_without_loading(amount):
    with pytest.raises(ValueError, match="finite nonnegative Decimal"):
        funding.plan(None, None, amount)


def test_plan_keeps_reconciliation_failure_technical(db_conn):
    w = world(db_conn)
    make_bill(db_conn, w, date(2026, 1, 1), "40")
    make_payment(db_conn, w.account, "60")
    with pytest.raises(InconsistentState, match="reconcile"):
        funding.plan(db_conn, w.account["id"], D("100"))
