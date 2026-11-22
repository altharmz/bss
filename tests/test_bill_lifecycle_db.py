"""Bill lifecycle slice 2: the billing run issues the bills it writes (new -> validated ->
sent, -> settled when nothing is due) and settles the account (unused credit pays open
bills, oldest first) - orders and payments through the API, runs through bill_account.

The invariant of docs/archive/bill-lifecycle-design.md 6.5 is checked after every scenario:
sum(remaining) - unallocated credit = -(account balance + unbilled ABRs)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import apply_result, bill_account, load_state
from app.billing.core import compute
from app.billing.model import InconsistentState
from app.repository import billing
from tests import apidata
from tests.builders.billing_db import add, at, bill, delete, run_row
from tests.timeline import TZ

D = Decimal


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300)


def bills(conn: psycopg.Connection, account: dict) -> list[tuple]:
    """(bill date, amount due, remaining, state) per bill."""
    return conn.execute(
        """
        SELECT b.bill_date, b.amount_due,
               CASE WHEN b.amount_due <= 0 THEN 0.00 ELSE b.amount_due - coalesce(
                   (SELECT sum(amount) FROM bill_allocation WHERE customer_bill_id = b.id), 0)
               END,
               b.state
        FROM customer_bill b WHERE b.billing_account_id = %s ORDER BY b.bill_date
        """,
        (account["id"],),
    ).fetchall()


def history(conn: psycopg.Connection, account: dict, day: date) -> list[tuple]:
    bill_id = conn.execute(
        "SELECT id FROM customer_bill WHERE billing_account_id = %s AND bill_date = %s",
        (account["id"], day),
    ).fetchone()[0]
    return [
        (h["from_state"], h["to_state"], h["cause"])
        for h in billing.bill_state_history(conn, bill_id)
    ]


def check_invariant(conn: psycopg.Connection, account: dict) -> None:
    account_id = UUID(account["id"])
    bill_rows, payments = billing.load_settlement(conn, account_id)
    remaining = sum((b.remaining for b in bill_rows if b.amount_due > 0), D("0"))
    unused = sum((p.left for p in payments), D("0")) + sum(
        (b.credit_left for b in bill_rows), D("0")
    )
    unbilled = conn.execute(
        "SELECT coalesce(sum(amount), 0) FROM applied_customer_billing_rate"
        " WHERE billing_account_id = %s AND customer_bill_id IS NULL",
        (account_id,),
    ).fetchone()[0]
    balance = billing.account_balance(conn, account_id)
    assert remaining - unused == -(balance + unbilled), (remaining, unused, balance, unbilled)


def counts(conn: psycopg.Connection) -> tuple[int, int, int]:
    return conn.execute(
        "SELECT (SELECT count(*) FROM bill_allocation),"
        " (SELECT count(*) FROM customer_bill_state_change),"
        " (SELECT count(*) FROM applied_customer_billing_rate)"
    ).fetchone()


def pay(api, account: dict, value: int, day: date) -> None:
    api.clock.set(at(day, hour=12))
    apidata.payment(api, account, value)


# --- issue ---


@pytest.mark.capability("worker.billIssue")
def test_the_run_issues_its_bill_and_running_it_again_changes_nothing(api, db_conn, offering):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    assert bills(db_conn, account) == [(date(2026, 12, 1), D("300.00"), D("300.00"), "sent")]
    assert history(db_conn, account, date(2026, 12, 1)) == [
        (None, "new", "run"),
        ("new", "validated", "run"),
        ("validated", "sent", "run"),
    ]
    before = counts(db_conn)
    bill(db_conn, account, date(2026, 12, 1))
    assert counts(db_conn) == before
    check_invariant(db_conn, account)


@pytest.mark.capability("worker.billIssue")
@pytest.mark.capability("worker.billSettle")
def test_a_credit_note_is_settled_at_issue_and_pays_the_older_open_bill(api, db_conn, offering):
    """Example 4 of stage 3: terminated Dec 20, the refund -116.13 makes the Jan 1 bill a
    credit note; its credit goes to the Dec 1 bill, which nobody has paid."""
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    delete(api, account, product, date(2026, 12, 20), on=at(date(2026, 12, 19)))
    bill(db_conn, account, date(2026, 12, 20))
    bill(db_conn, account, date(2027, 1, 1))
    assert bills(db_conn, account) == [
        (date(2026, 12, 1), D("300.00"), D("183.87"), "partiallyPaid"),
        (date(2027, 1, 1), D("-116.13"), D("0.00"), "settled"),
    ]
    assert history(db_conn, account, date(2027, 1, 1))[-1] == ("sent", "settled", "run")
    assert history(db_conn, account, date(2026, 12, 1))[-1] == ("sent", "partiallyPaid", "run")
    category = db_conn.execute("SELECT category FROM customer_bill WHERE amount_due < 0").fetchone()
    assert category == ("creditNote",)
    check_invariant(db_conn, account)


# --- settlement in every run ---


@pytest.mark.capability("worker.billSettle")
def test_payments_settle_bills_in_the_runs(api, db_conn, offering):
    """500 paid before the first bill settles it at issue and leaves 200 of credit for the
    January bill; 100 paid on Jan 5 settles January in the run of Jan 6 - a run that
    issues no bill (payments settle at once from slice 3 on)."""
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    pay(api, account, 500, date(2026, 11, 25))
    bill(db_conn, account, date(2026, 12, 1))
    assert bills(db_conn, account) == [(date(2026, 12, 1), D("300.00"), D("0.00"), "settled")]
    assert history(db_conn, account, date(2026, 12, 1))[-1] == ("sent", "settled", "run")
    check_invariant(db_conn, account)

    bill(db_conn, account, date(2027, 1, 1))
    assert bills(db_conn, account)[1] == (
        date(2027, 1, 1),
        D("300.00"),
        D("100.00"),
        "partiallyPaid",
    )
    check_invariant(db_conn, account)

    pay(api, account, 100, date(2027, 1, 5))
    bill(db_conn, account, date(2027, 1, 6))
    assert bills(db_conn, account)[1] == (date(2027, 1, 1), D("300.00"), D("0.00"), "settled")
    check_invariant(db_conn, account)


# --- bills from before migration 0006, and the validation ---


def write_without_issuing(db_conn, account: dict, day: date) -> None:
    """What every run before slice 2 did: write the bill and leave it `new`."""
    run = run_row(db_conn, day)
    state = load_state(db_conn, UUID(account["id"]), day, TZ)
    with db_conn.transaction():
        apply_result(
            db_conn, state.account, run["id"], compute(state, day, TZ), issued_at=run["started_at"]
        )
    db_conn.commit()


@pytest.mark.capability("worker.billIssue")
@pytest.mark.capability("worker.billSettle")
def test_a_bill_left_new_is_issued_and_settled_by_the_next_run(api, db_conn, offering):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    pay(api, account, 300, date(2026, 11, 25))
    write_without_issuing(db_conn, account, date(2026, 12, 1))
    assert bills(db_conn, account) == [(date(2026, 12, 1), D("300.00"), D("300.00"), "new")]
    bill(db_conn, account, date(2026, 12, 2))
    assert bills(db_conn, account) == [(date(2026, 12, 1), D("300.00"), D("0.00"), "settled")]
    check_invariant(db_conn, account)


@pytest.mark.capability("worker.billIssue")
@pytest.mark.parametrize(
    "tamper",
    [
        # (ABRs detached from the bill never reach the validation: the core already
        # refuses an unbilled ABR older than the last bill)
        "UPDATE customer_bill SET amount_due = 299.99",
        "UPDATE customer_bill SET bill_period = daterange(bill_date, bill_date + 30)",
    ],
)
def test_a_bill_that_fails_validation_fails_the_account(api, db_conn, offering, tamper):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    write_without_issuing(db_conn, account, date(2026, 12, 1))
    db_conn.execute(tamper)
    db_conn.commit()
    before = counts(db_conn)
    with pytest.raises(InconsistentState):
        bill_account(db_conn, UUID(account["id"]), run_row(db_conn, date(2026, 12, 2)))
    db_conn.rollback()
    assert counts(db_conn) == before
    assert [b[3] for b in bills(db_conn, account)] == ["new"]
