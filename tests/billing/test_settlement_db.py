"""Settlement against the real schema (migration 0006): the repository round trip, the
amount bounds (a deferred constraint trigger, checked at commit), the composite foreign
keys and the state history."""

from datetime import date

import psycopg
import pytest
from psycopg import errors

from app.billing import settlement
from app.billing.model import Account
from app.repository import billing
from tests.builders.billing.settlement_db import (
    D,
    allocate,
    make_bill,
    make_payment,
    settle_account,
)
from tests.factories import NOW, account, rejected, world


def states(conn, acc) -> list[tuple[date, str]]:
    return conn.execute(
        "SELECT bill_date, state FROM customer_bill WHERE billing_account_id = %s"
        " ORDER BY bill_date",
        (acc["id"],),
    ).fetchall()


# --- the repository round trip ---


def test_settle_write_and_reload(db_conn):
    w = world(db_conn)
    acc = w.account
    jan = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    feb = make_bill(db_conn, w, date(2026, 2, 1), "100.00")
    note = make_bill(db_conn, w, date(2026, 3, 1), "-30.00", state="settled")
    pay = make_payment(db_conn, acc, "150.00")
    db_conn.commit()

    result = settle_account(db_conn, acc)
    db_conn.commit()
    assert [(a.bill_id, a.amount) for a in result.allocations] == [
        (jan["id"], D("100.00")),
        (feb["id"], D("50.00")),
        (feb["id"], D("30.00")),
    ]
    assert states(db_conn, acc) == [
        (date(2026, 1, 1), "settled"),
        (date(2026, 2, 1), "partiallyPaid"),
        (date(2026, 3, 1), "settled"),
    ]
    bills, pays = billing.load_settlement(db_conn, acc["id"])
    by_id = {b.id: b for b in bills}
    assert by_id[feb["id"]].remaining == D("20.00")
    assert by_id[note["id"]].credit_left == 0
    assert [p.left for p in pays] == [0]
    assert settle_account(db_conn, acc).is_empty  # nothing left to do

    history = [
        (r["from_state"], r["to_state"], r["cause"], r["changed_at"])
        for r in billing.bill_state_history(db_conn, feb["id"])
    ]
    assert history == [("sent", "partiallyPaid", "payment", NOW)]
    sources = db_conn.execute(
        "SELECT payment_id, credit_bill_id, amount FROM bill_allocation"
        " WHERE customer_bill_id = %s ORDER BY amount DESC",
        (feb["id"],),
    ).fetchall()
    assert sources == [(pay["id"], None, D("50.00")), (None, note["id"], D("30.00"))]


def test_a_payment_without_a_state_change_still_moves_last_update(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00", state="partiallyPaid")
    first = make_payment(db_conn, w.account, "10.00")
    allocate(db_conn, w.account, bill["id"], "10.00", payment_id=first["id"])
    db_conn.commit()
    make_payment(db_conn, w.account, "20.00", minutes=1)
    db_conn.commit()
    result = settle_account(db_conn, w.account)
    db_conn.commit()
    assert result.changes == ()
    updated = billing.get_bill(db_conn, bill["id"])["updated_at"]
    assert updated > bill["updated_at"]


def test_a_state_change_needs_the_expected_from_state(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    change = settlement.StateChange(bill["id"], "partiallyPaid", "settled")
    with pytest.raises(LookupError):
        billing.record_bill_state(db_conn, change, now=NOW, cause="payment")


# --- the amount bounds: a deferred constraint trigger ---


def commit_fails(conn: psycopg.Connection, match: str) -> None:
    with pytest.raises(errors.CheckViolation, match=match):
        conn.commit()
    conn.rollback()


def test_a_bill_cannot_be_over_allocated(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    pay = make_payment(db_conn, w.account, "500.00")
    db_conn.commit()
    allocate(db_conn, w.account, bill["id"], "60.00", payment_id=pay["id"])
    allocate(db_conn, w.account, bill["id"], "40.01", payment_id=pay["id"])
    commit_fails(db_conn, "over-allocated")
    allocate(db_conn, w.account, bill["id"], "100.00", payment_id=pay["id"])
    db_conn.commit()  # exactly the amount due is fine


def test_a_payment_cannot_be_over_allocated(db_conn):
    w = world(db_conn)
    a = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    b = make_bill(db_conn, w, date(2026, 2, 1), "100.00")
    pay = make_payment(db_conn, w.account, "150.00")
    db_conn.commit()
    allocate(db_conn, w.account, a["id"], "100.00", payment_id=pay["id"])
    allocate(db_conn, w.account, b["id"], "50.01", payment_id=pay["id"])
    commit_fails(db_conn, "payment .* over-allocated")


def test_a_credit_note_cannot_give_more_than_its_credit(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    note = make_bill(db_conn, w, date(2026, 2, 1), "-30.00", state="settled")
    db_conn.commit()
    allocate(db_conn, w.account, bill["id"], "30.01", credit_bill_id=note["id"])
    commit_fails(db_conn, "credit note .* over-allocated")


@pytest.mark.parametrize("amount_due", ["0.00", "-10.00"])
def test_only_a_positive_bill_can_be_paid(db_conn, amount_due):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), amount_due, state="settled")
    pay = make_payment(db_conn, w.account, "10.00")
    db_conn.commit()
    allocate(db_conn, w.account, bill["id"], "1.00", payment_id=pay["id"])
    commit_fails(db_conn, "nothing to pay")


def test_only_a_negative_bill_is_a_credit_note(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    other = make_bill(db_conn, w, date(2026, 2, 1), "10.00")
    db_conn.commit()
    allocate(db_conn, w.account, bill["id"], "1.00", credit_bill_id=other["id"])
    commit_fails(db_conn, "not a credit note")


# --- shape: one source, one account, one currency ---


def test_exactly_one_source(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    note = make_bill(db_conn, w, date(2026, 2, 1), "-30.00", state="settled")
    pay = make_payment(db_conn, w.account, "10.00")
    with rejected(db_conn, errors.CheckViolation):
        allocate(db_conn, w.account, bill["id"], "1.00")
    with rejected(db_conn, errors.CheckViolation):
        allocate(
            db_conn, w.account, bill["id"], "1.00", payment_id=pay["id"], credit_bill_id=note["id"]
        )
    with rejected(db_conn, errors.CheckViolation):
        allocate(db_conn, w.account, note["id"], "1.00", credit_bill_id=note["id"])
    with rejected(db_conn, errors.CheckViolation):
        allocate(db_conn, w.account, bill["id"], "0.00", payment_id=pay["id"])


def test_bill_and_source_belong_to_the_same_account(db_conn):
    w = world(db_conn)
    stranger = account(db_conn, name="Petrov")
    mine = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    theirs = make_bill(db_conn, w, date(2026, 1, 1), "100.00", acc=stranger)
    their_note = make_bill(db_conn, w, date(2026, 2, 1), "-5.00", state="settled", acc=stranger)
    their_pay = make_payment(db_conn, stranger, "10.00")
    with rejected(db_conn, errors.ForeignKeyViolation):
        allocate(db_conn, w.account, mine["id"], "1.00", payment_id=their_pay["id"])
    with rejected(db_conn, errors.ForeignKeyViolation):
        allocate(db_conn, w.account, mine["id"], "1.00", credit_bill_id=their_note["id"])
    with rejected(db_conn, errors.ForeignKeyViolation):
        allocate(db_conn, w.account, theirs["id"], "1.00", payment_id=their_pay["id"])


def test_the_allocation_is_in_the_account_currency(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "100.00")
    pay = make_payment(db_conn, w.account, "10.00")
    with rejected(db_conn, errors.ForeignKeyViolation):
        allocate(
            db_conn, {**w.account, "currency": "EUR"}, bill["id"], "1.00", payment_id=pay["id"]
        )


# --- customer_bill: category and due date ---


def test_category_follows_the_sign_of_the_amount(db_conn):
    w = world(db_conn)
    assert make_bill(db_conn, w, date(2026, 1, 1), "-1.00", state="settled")["category"] == (
        "creditNote"
    )
    assert make_bill(db_conn, w, date(2026, 2, 1), "0.00", state="settled")["category"] == "normal"
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE customer_bill SET category = 'creditNote' WHERE amount_due >= 0")


def test_the_due_date_is_never_before_the_bill_date(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "1.00")
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            "UPDATE customer_bill SET payment_due_date = bill_date - 1 WHERE id = %s",
            (bill["id"],),
        )


# --- customer_bill_state_change ---


def test_state_history_rows_are_changes_and_name_an_operator_only_for_the_operator(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "1.00")

    def row(from_state, to_state, cause, operator=None):
        db_conn.execute(
            "INSERT INTO customer_bill_state_change"
            " (customer_bill_id, from_state, to_state, changed_at, cause, operator)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (bill["id"], from_state, to_state, NOW, cause, operator),
        )

    row("sent", "onHold", "operator", "alice")
    row(None, "new", "run")
    with rejected(db_conn, errors.CheckViolation):
        row("sent", "sent", "run")
    with rejected(db_conn, errors.CheckViolation):
        row("sent", "onHold", "payment", "alice")


def test_write_settlement_records_the_operator(db_conn):
    w = world(db_conn)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "1.00")
    change = settlement.StateChange(bill["id"], "sent", "onHold")
    core = Account(id=w.account["id"], currency="USD", bill_cycle_day=1)
    billing.write_settlement(
        db_conn,
        core,
        settlement.Settlement((), (change,)),
        now=NOW,
        cause="operator",
        operator="alice",
    )
    [entry] = billing.bill_state_history(db_conn, bill["id"])
    assert (entry["to_state"], entry["cause"], entry["operator"]) == ("onHold", "operator", "alice")
