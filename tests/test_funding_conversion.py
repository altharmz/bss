"""Durable exact-source conversion without a public prepaid consuming path."""

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors

from alembic import command
from app.billing.model import InconsistentState
from app.repository import funding
from tests.factories import NOW, account, rejected, world
from tests.test_funding_db import billed_charge
from tests.test_settlement_db import allocate, make_bill, make_payment


def prepared(conn, amount="15"):
    w = world(conn)
    make_payment(conn, w.account, "20")
    key = uuid4()
    hold = funding.reserve_for_command(
        conn, w.account["id"], D(amount), command_id=key, held_at=NOW
    )
    bill = billed_charge(conn, w, date(2026, 1, 1), amount)
    return w, key, hold, bill


def convert(conn, w, key, bill, at=NOW):
    return funding.convert_for_command(
        conn, w.account["id"], bill["id"], command_id=key, converted_at=at
    )


def test_conversion_commit_retry_and_conflicting_target(db_conn):
    w, key, hold, bill = prepared(db_conn)
    assert convert(db_conn, w, key, bill) == hold
    db_conn.commit()
    assert convert(db_conn, w, key, bill, NOW + timedelta(days=1)) == hold
    assert db_conn.execute("SELECT amount, allocated_at FROM bill_allocation").fetchall() == [
        (D(15), NOW)
    ]
    assert db_conn.execute(
        "SELECT state FROM customer_bill WHERE id = %s", (bill["id"],)
    ).fetchone() == ("settled",)
    assert db_conn.execute("SELECT count(*) FROM customer_bill_state_change").fetchone() == (1,)
    assert funding.load(db_conn, w.account["id"]).spendable == D(5)
    with pytest.raises(ValueError, match="target mismatch"):
        convert(db_conn, w, key, {"id": uuid4()})
    with pytest.raises(ValueError, match="unknown funding command"):
        funding.convert_for_command(
            db_conn, account(db_conn)["id"], bill["id"], command_id=key, converted_at=NOW
        )


def test_conversion_uses_exact_mixed_sources_without_double_counting(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2025, 12, 1), "10")
    note = billed_charge(db_conn, w, date(2026, 1, 1), "-30", "settled")
    payment = make_payment(db_conn, w.account, "5")
    key = uuid4()
    hold = funding.reserve_for_command(db_conn, w.account["id"], D(20), command_id=key, held_at=NOW)
    bill = billed_charge(db_conn, w, date(2026, 2, 1), "20")
    assert convert(db_conn, w, key, bill) == hold
    db_conn.commit()
    assert db_conn.execute(
        "SELECT payment_id, credit_bill_id, amount FROM bill_allocation ORDER BY amount"
    ).fetchall() == [(payment["id"], None, D(5)), (None, note["id"], D(15))]
    snapshot = funding.load(db_conn, w.account["id"])
    assert snapshot.ledger == snapshot.spendable == D(5)
    assert snapshot.unpaid_obligations == D(10)
    assert sum(s.held for s in snapshot.sources) == 0


def test_conversion_rollback_restores_hold_and_can_retry(db_conn):
    w, key, hold, bill = prepared(db_conn)
    db_conn.commit()
    convert(db_conn, w, key, bill)
    db_conn.rollback()
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)
    assert convert(db_conn, w, key, bill) == hold
    db_conn.commit()


def test_conversion_failure_restores_release_and_allocations(db_conn):
    w = world(db_conn)
    make_payment(db_conn, w.account, "20")
    key = uuid4()
    funding.reserve_for_command(db_conn, w.account["id"], D(15), command_id=key, held_at=NOW)
    bill = make_bill(db_conn, w, date(2026, 1, 1), "15")  # Deliberately no backing charge.
    with pytest.raises(InconsistentState, match="reconcile"):
        convert(db_conn, w, key, bill)
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
    assert db_conn.execute("SELECT count(*) FROM funding_hold_conversion").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)
    assert db_conn.execute("SELECT state FROM customer_bill").fetchone() == ("sent",)


@pytest.mark.parametrize("case", ["released", "earlier", "amount", "held", "foreign", "unknown"])
def test_conversion_rejects_invalid_request_without_consuming(db_conn, case):
    w, key, hold, bill = prepared(db_conn)
    at = NOW
    if case == "released":
        funding.release(db_conn, w.account["id"], hold, released_at=NOW, command_id=key)
    elif case == "earlier":
        at -= timedelta(seconds=1)
    elif case == "amount":
        bill = make_bill(db_conn, w, date(2026, 2, 1), "14")
    elif case == "held":
        db_conn.execute("UPDATE customer_bill SET state = 'onHold' WHERE id = %s", (bill["id"],))
    elif case == "foreign":
        bill = make_bill(db_conn, w, date(2026, 2, 1), "15", acc=account(db_conn))
    else:
        key = uuid4()
    with pytest.raises(ValueError):
        convert(db_conn, w, key, bill, at)
    assert db_conn.execute("SELECT count(*) FROM funding_hold_conversion").fetchone() == (0,)


def test_zero_conversion_has_no_source_allocations(db_conn):
    w, key, hold, bill = prepared(db_conn, "0")
    assert convert(db_conn, w, key, bill) == hold
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM funding_conversion_source").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)


def test_conversion_history_is_immutable_and_exact(db_conn):
    w, key, hold, bill = prepared(db_conn)
    db_conn.commit()
    # A journal entry alone cannot claim consumption of an outstanding hold.
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            "INSERT INTO funding_hold_conversion VALUES (%s, %s, %s, %s)",
            (hold, w.account["id"], bill["id"], NOW),
        )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    convert(db_conn, w, key, bill)
    db_conn.commit()
    for statement in (
        "DELETE FROM funding_hold_conversion",
        "UPDATE funding_hold_conversion SET hold_id = gen_random_uuid()",
        "DELETE FROM funding_conversion_source",
        "UPDATE funding_conversion_source SET hold_id = gen_random_uuid()",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE customer_bill SET state = 'onHold' WHERE id = %s", (bill["id"],))
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_conversion_retries_serialize(db_conn, migrated_db_url):
    w, key, hold, bill = prepared(db_conn)
    db_conn.commit()
    convert(db_conn, w, key, bill)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            convert(other, w, key, bill)
        other.rollback()
        db_conn.commit()
        assert convert(other, w, key, bill) == hold


@pytest.mark.parametrize("mismatch", ["source", "amount", "instant", "missing"])
def test_direct_sql_cannot_claim_inexact_conversion(db_conn, mismatch):
    w, key, hold, bill = prepared(db_conn)
    other_payment = make_payment(db_conn, w.account, "20", minutes=1)
    held_source = db_conn.execute(
        "SELECT id, payment_id FROM funding_hold_source WHERE hold_id = %s", (hold,)
    ).fetchone()
    db_conn.commit()
    with rejected(db_conn, errors.CheckViolation):
        funding.release(db_conn, w.account["id"], hold, released_at=NOW, command_id=key)
        db_conn.execute("UPDATE customer_bill SET state = 'settled' WHERE id = %s", (bill["id"],))
        db_conn.execute(
            "INSERT INTO funding_hold_conversion VALUES (%s, %s, %s, %s)",
            (hold, w.account["id"], bill["id"], NOW),
        )
        if mismatch != "missing":
            allocation = db_conn.execute(
                "INSERT INTO bill_allocation (billing_account_id, currency, customer_bill_id,"
                " payment_id, amount, kind, allocated_at)"
                " VALUES (%s, 'USD', %s, %s, %s, 'automatic', %s) RETURNING id",
                (
                    w.account["id"],
                    bill["id"],
                    other_payment["id"] if mismatch == "source" else held_source[1],
                    D(14) if mismatch == "amount" else D(15),
                    NOW + timedelta(seconds=1) if mismatch == "instant" else NOW,
                ),
            ).fetchone()[0]
            db_conn.execute(
                "INSERT INTO funding_conversion_source VALUES (%s, %s, %s, %s)",
                (held_source[0], hold, allocation, w.account["id"]),
            )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_conversion_does_not_adopt_existing_allocations(db_conn):
    w, key, hold, bill = prepared(db_conn)
    payment = make_payment(db_conn, w.account, "10", minutes=1)
    allocate(db_conn, w.account, bill["id"], "1", payment_id=payment["id"])
    with pytest.raises(ValueError, match="already has allocations"):
        convert(db_conn, w, key, bill)
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (None,)


@pytest.mark.migration_history
def test_conversion_migration_preserves_holds_and_retains_results(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0036")
    with psycopg.connect(temp_db_url) as conn:
        w, key, hold, bill = prepared(conn)
    command.upgrade(cfg, "0037")
    command.downgrade(cfg, "0036")
    command.upgrade(cfg, "0037")
    with psycopg.connect(temp_db_url) as conn:
        assert convert(conn, w, key, bill) == hold
    with pytest.raises(Exception, match="funding conversion history must be retained"):
        command.downgrade(cfg, "0036")
