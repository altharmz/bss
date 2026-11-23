"""Retained holds share bounds with legacy allocations and payouts."""

from datetime import date, timedelta
from decimal import Decimal as D

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors

from alembic import command
from app.billing.funding import InsufficientFunds
from app.repository import funding
from tests.builders.funding_db import billed_charge
from tests.builders.settlement_db import allocate, make_payment
from tests.factories import NOW, account, rejected, world


def test_settlement_skips_mixed_held_sources_and_release_restores_them(db_conn):
    from tests.builders.settlement_db import (
        settle_account,
    )

    w = world(db_conn)
    make_payment(db_conn, w.account, "6")
    billed_charge(db_conn, w, date(2025, 12, 1), "1")
    billed_charge(db_conn, w, date(2026, 1, 1), "-20", "settled")
    hold = funding.reserve(db_conn, w.account["id"], D("12"), held_at=NOW)
    billed_charge(db_conn, w, date(2026, 2, 1), "20")
    result = settle_account(db_conn, w.account)
    assert sum(a.amount for a in result.allocations) == D("14")
    assert all(a.payment_id is None for a in result.allocations)
    db_conn.commit()
    funding.release(db_conn, w.account["id"], hold, released_at=NOW)
    result = settle_account(db_conn, w.account)
    assert sum(a.amount for a in result.allocations) == D("7")
    db_conn.commit()
    assert funding.load(db_conn, w.account["id"]).spendable == D("5")


def test_explicit_settlement_refuses_held_payment(db_conn):
    from app.billing.adapter import settle_account
    from app.billing.settlement import ExceedsPayment, ExplicitItem
    from app.repository import billing

    w = world(db_conn)
    payment = make_payment(db_conn, w.account, "30")
    funding.reserve(db_conn, w.account["id"], D("20"), held_at=NOW)
    bill = billed_charge(db_conn, w, date(2026, 1, 1), "15")
    with pytest.raises(ExceedsPayment):
        settle_account(
            db_conn,
            billing.load_account(db_conn, w.account["id"]),
            now=NOW,
            cause="payment",
            explicit=(ExplicitItem(bill["id"], D("11")),),
            explicit_payment_id=payment["id"],
        )
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)


def test_hold_mixed_sources_and_release_once(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "10")
    note = billed_charge(db_conn, w, date(2026, 2, 1), "-20", "settled")
    payment = make_payment(db_conn, w.account, "5")
    hold = funding.reserve(db_conn, w.account["id"], D("12.34"), held_at=NOW)
    db_conn.commit()
    snap = funding.load(db_conn, w.account["id"])
    assert snap.ledger == D("15")
    assert snap.spendable == D("2.66")
    assert {(s.kind, s.id): s.held for s in snap.sources} == {
        ("payment", payment["id"]): D("5"),
        ("creditNote", note["id"]): D("7.34"),
    }
    with pytest.raises(InsufficientFunds):
        funding.reserve(db_conn, w.account["id"], D("2.67"), held_at=NOW)
    funding.release(db_conn, w.account["id"], hold, released_at=NOW)
    funding.release(db_conn, w.account["id"], hold, released_at=NOW + timedelta(days=1))
    db_conn.commit()
    assert funding.load(db_conn, w.account["id"]).spendable == D("15")
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (NOW,)


@pytest.mark.parametrize("consumer", ["hold", "allocation", "refund"])
def test_combined_bounds_reject_legacy_or_hold_overspend(db_conn, consumer):
    w = world(db_conn)
    bill = billed_charge(db_conn, w, date(2026, 1, 1), "10")
    pay = make_payment(db_conn, w.account, "30")
    funding.reserve(db_conn, w.account["id"], D("20"), held_at=NOW)
    allocate(db_conn, w.account, bill["id"], "5", payment_id=pay["id"])
    db_conn.commit()
    with rejected(db_conn, errors.CheckViolation):
        if consumer == "allocation":
            allocate(db_conn, w.account, bill["id"], "6", payment_id=pay["id"])
        elif consumer == "refund":
            db_conn.execute(
                "WITH r AS (INSERT INTO refund"
                " (billing_account_id, currency, amount, refunded_at, method_name)"
                " VALUES (%s, 'USD', 6, %s, 'cash') RETURNING id)"
                " INSERT INTO refund_source (refund_id, billing_account_id, payment_id, amount)"
                " SELECT id, %s, %s, 6 FROM r",
                (w.account["id"], NOW, w.account["id"], pay["id"]),
            )
        else:
            db_conn.execute(
                "WITH h AS (INSERT INTO funding_hold"
                " (billing_account_id, currency, amount, held_at)"
                " VALUES (%s, 'USD', 6, %s) RETURNING id)"
                " INSERT INTO funding_hold_source (hold_id, billing_account_id, payment_id, amount)"
                " SELECT id, %s, %s, 6 FROM h",
                (w.account["id"], NOW, w.account["id"], pay["id"]),
            )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_zero_hold_retains_history_and_owner(db_conn):
    acc = account(db_conn)
    other = account(db_conn, name="Other")
    hold = funding.reserve(db_conn, acc["id"], D(0), held_at=NOW)
    db_conn.commit()
    with pytest.raises(ValueError, match="unknown"):
        funding.release(db_conn, other["id"], hold, released_at=NOW)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],)
        )
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("DELETE FROM funding_hold")
    funding.release(db_conn, acc["id"], hold, released_at=NOW)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE funding_hold SET released_at = NULL")


def test_hold_details_are_immutable_and_totals_must_match(db_conn):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    funding.reserve(db_conn, acc["id"], D("10"), held_at=NOW)
    db_conn.commit()
    for statement in (
        "UPDATE funding_hold_source SET amount = 9",
        "DELETE FROM funding_hold_source",
        "UPDATE funding_hold SET amount = 9",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            "INSERT INTO funding_hold (billing_account_id, currency, amount, held_at)"
            " VALUES (%s, 'USD', 1, %s)",
            (acc["id"], NOW),
        )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.migration_history
def test_hold_downgrade_retains_released_history(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0034")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        make_payment(conn, acc, "20")
    command.upgrade(cfg, "0035")
    command.downgrade(cfg, "0034")
    command.upgrade(cfg, "0035")
    with psycopg.connect(temp_db_url) as conn:
        assert funding.load(conn, acc["id"]).spendable == D("20")
        hold = funding.reserve(conn, acc["id"], D("10"), held_at=NOW)
        # This proof deliberately stays at 0035, before command ownership exists.
        conn.execute("UPDATE funding_hold SET released_at = %s WHERE id = %s", (NOW, hold))
    with pytest.raises(Exception, match="funding hold history must be retained"):
        command.downgrade(cfg, "0034")


@pytest.mark.parametrize(
    "amount", [D("0.001"), D("NaN"), D("Infinity"), D("-1"), D("10000000000"), 1]
)
def test_hold_refuses_amounts_that_would_round_or_overflow(amount):
    with pytest.raises(ValueError, match="exact cents"):
        funding.reserve(None, None, amount, held_at=NOW)


def test_direct_sql_holds_serialize_with_repository(db_conn, migrated_db_url):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    db_conn.commit()
    funding.reserve(db_conn, acc["id"], D("15"), held_at=NOW)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            other.execute(
                "INSERT INTO funding_hold (billing_account_id, currency, amount, held_at)"
                " VALUES (%s, 'USD', 0, %s)",
                (acc["id"], NOW),
            )
        other.rollback()
        db_conn.commit()
        with pytest.raises(InsufficientFunds) as exc:
            funding.reserve(other, acc["id"], D("6"), held_at=NOW)
        assert exc.value.available == D("5")


def test_credit_note_combined_bounds_and_source_owner(db_conn):
    w = world(db_conn)
    billed_charge(db_conn, w, date(2026, 1, 1), "10")
    note = billed_charge(db_conn, w, date(2026, 2, 1), "-30", "settled")
    hold = funding.reserve(db_conn, w.account["id"], D("20"), held_at=NOW)
    other = account(db_conn, name="Other")
    foreign_payment = make_payment(db_conn, other, "50")
    db_conn.commit()
    with rejected(db_conn, errors.ForeignKeyViolation):
        db_conn.execute(
            "INSERT INTO funding_hold_source (hold_id, billing_account_id, payment_id, amount)"
            " VALUES (%s, %s, %s, 1)",
            (hold, w.account["id"], foreign_payment["id"]),
        )
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(
            "WITH r AS (INSERT INTO refund"
            " (billing_account_id, currency, amount, refunded_at, method_name)"
            " VALUES (%s, 'USD', 11, %s, 'cash') RETURNING id)"
            " INSERT INTO refund_source (refund_id, billing_account_id, credit_bill_id, amount)"
            " SELECT id, %s, %s, 11 FROM r",
            (w.account["id"], NOW, w.account["id"], note["id"]),
        )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE customer_bill SET state = 'sent' WHERE id = %s", (note["id"],))
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
