"""Exact source splitting, atomic recovery and retained multi-bill results."""

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors, sql

from alembic import command
from app.repository import funding
from tests.builders.funding_conversion import prepared as single_prepared
from tests.builders.funding_db import billed_charge
from tests.builders.settlement_db import allocate, make_bill, make_payment
from tests.factories import NOW, World, account, offering, product, rejected, world


def prepared(conn, *, zero=False, existing_run=None):
    if existing_run is None:
        w = world(conn)
    else:
        off, acc = offering(conn), account(conn)
        prod = product(conn, acc["id"], off["id"], date(2026, 1, 1))
        w = World(off, acc, prod, existing_run)
    make_payment(conn, w.account, "15")
    billed_charge(conn, w, date(2025, 11, 1), "10")
    billed_charge(conn, w, date(2025, 12, 1), "-30", "settled")
    key = uuid4()
    hold = funding.reserve_for_command(
        conn, w.account["id"], D(0 if zero else 20), command_id=key, held_at=NOW
    )
    bills = [
        billed_charge(conn, w, date(2026, month, 1), amount)
        for month, amount in [(1, "0" if zero else "8"), (2, "0" if zero else "12")]
    ]
    return w, key, hold, [b["id"] for b in bills]


def convert(conn, w, key, targets, at=NOW):
    return funding.convert_bills_for_command(
        conn, w.account["id"], targets, command_id=key, converted_at=at
    )


@pytest.mark.parametrize("zero", [False, True])
def test_split_exact_sources_replay_and_retained_targets(db_conn, zero):
    w, key, hold, bills = prepared(db_conn, zero=zero)
    assert convert(db_conn, w, key, bills) == hold
    db_conn.commit()
    assert convert(db_conn, w, key, list(reversed(bills)), NOW + timedelta(days=1)) == hold
    assert db_conn.execute(
        "SELECT b.state, b.amount_due, coalesce(sum(a.amount), 0) FROM customer_bill b"
        " LEFT JOIN bill_allocation a ON a.customer_bill_id = b.id"
        " WHERE b.id = ANY(%s) GROUP BY b.id ORDER BY b.amount_due",
        (bills,),
    ).fetchall() == [("settled", D(0 if zero else n), D(0 if zero else n)) for n in (8, 12)]
    assert db_conn.execute(
        "SELECT s.amount, sum(a.amount) FROM funding_hold_source s"
        " JOIN funding_conversion_source m ON m.hold_source_id = s.id"
        " JOIN bill_allocation a ON a.id = m.allocation_id GROUP BY s.id ORDER BY s.amount"
    ).fetchall() == ([] if zero else [(D(5), D(5)), (D(15), D(15))])
    if not zero:
        assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (3,)
    assert funding.load(db_conn, w.account["id"]).spendable == D(35 if zero else 15)
    for targets in (bills[:1], [bills[0], uuid4()], bills + [bills[0]]):
        with pytest.raises(ValueError):
            convert(db_conn, w, key, targets)
    tables = ["funding_conversion_bill", "funding_hold_conversion"]
    if not zero:
        tables.append("funding_conversion_source")
    for table in tables:
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE customer_bill SET state = 'onHold' WHERE id = %s", (bills[0],))
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "case", ["empty", "foreign", "amount", "held", "allocated", "released", "earlier"]
)
def test_invalid_batch_retains_hold(db_conn, case):
    w, key, hold, bills = prepared(db_conn)
    at = NOW
    if case == "empty":
        bills = []
    elif case == "foreign":
        bills[0] = make_bill(db_conn, w, NOW.date(), "8", acc=account(db_conn))["id"]
    elif case == "amount":
        bills = bills[:1]
    elif case == "held":
        db_conn.execute("UPDATE customer_bill SET state = 'onHold' WHERE id = %s", (bills[0],))
    elif case == "allocated":
        pay = make_payment(db_conn, w.account, "1", minutes=1)
        allocate(db_conn, w.account, bills[0], "1", payment_id=pay["id"])
    elif case == "released":
        funding.release(db_conn, w.account["id"], hold, command_id=key, released_at=NOW)
    else:
        at -= timedelta(seconds=1)
    with pytest.raises(ValueError):
        convert(db_conn, w, key, bills, at)
    assert db_conn.execute("SELECT count(*) FROM funding_hold_conversion").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM funding_conversion_bill").fetchone() == (0,)


def test_batch_rollback_and_concurrent_replay(db_conn, migrated_db_url):
    w, key, hold, bills = prepared(db_conn)
    db_conn.commit()
    convert(db_conn, w, key, bills)
    db_conn.rollback()
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)
    convert(db_conn, w, key, bills)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            convert(other, w, key, bills)
        other.rollback()
        db_conn.commit()
        assert convert(other, w, key, bills) == hold


@pytest.mark.parametrize(
    "mismatch", ["source", "instant", "missing_mapping", "missing_bill", "total", "empty"]
)
def test_database_refuses_inexact_batch_conversion(db_conn, mismatch):
    w, key, hold, bills = prepared(db_conn)
    other_payment = make_payment(db_conn, w.account, "100", minutes=1)
    sources = db_conn.execute(
        "SELECT id, payment_id, credit_bill_id FROM funding_hold_source"
        " WHERE hold_id = %s ORDER BY payment_id NULLS LAST",
        (hold,),
    ).fetchall()
    bills = [
        r[0]
        for r in db_conn.execute(
            "SELECT id FROM customer_bill WHERE id = ANY(%s) ORDER BY amount_due", (bills,)
        )
    ]
    db_conn.commit()
    with rejected(db_conn, errors.CheckViolation):
        funding.release(db_conn, w.account["id"], hold, command_id=key, released_at=NOW)
        db_conn.execute(
            "INSERT INTO funding_hold_conversion (hold_id, billing_account_id, converted_at)"
            " VALUES (%s,%s,%s)",
            (hold, w.account["id"], NOW),
        )
        if mismatch != "empty":
            for bill in bills[:1] if mismatch == "missing_bill" else bills:
                db_conn.execute(
                    "INSERT INTO funding_conversion_bill VALUES (%s,%s,%s)",
                    (bill, hold, w.account["id"]),
                )
            db_conn.execute(
                "UPDATE customer_bill SET state = 'settled' WHERE id = ANY(%s)", (bills,)
            )
            for bill, amount, source in [
                (bills[0], D(8), sources[0]),
                (bills[1], D(7), sources[0]),
                (bills[1], D(5), sources[1]),
            ]:
                allocation = db_conn.execute(
                    "INSERT INTO bill_allocation (billing_account_id, currency, customer_bill_id,"
                    " payment_id, credit_bill_id, amount, kind, allocated_at)"
                    " VALUES (%s,'USD',%s,%s,%s,%s,'automatic',%s) RETURNING id",
                    (
                        w.account["id"],
                        bill,
                        other_payment["id"] if mismatch == "source" and source[1] else source[1],
                        source[2],
                        amount - 1 if mismatch == "total" else amount,
                        NOW + timedelta(seconds=1) if mismatch == "instant" else NOW,
                    ),
                ).fetchone()[0]
                if mismatch != "missing_mapping":
                    db_conn.execute(
                        "INSERT INTO funding_conversion_source VALUES (%s,%s,%s,%s)",
                        (source[0], hold, allocation, w.account["id"]),
                    )
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.migration_history
def test_batch_migration_preserves_legacy_results_and_refuses_loss(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0040")
    with psycopg.connect(temp_db_url) as conn:
        w, key, hold, bill = single_prepared(conn)
        funding.convert_for_command(
            conn, w.account["id"], bill["id"], command_id=key, converted_at=NOW
        )
    command.upgrade(cfg, "0041")
    command.downgrade(cfg, "0040")
    command.upgrade(cfg, "0041")
    with psycopg.connect(temp_db_url) as conn:
        assert (
            funding.convert_for_command(
                conn, w.account["id"], bill["id"], command_id=key, converted_at=NOW
            )
            == hold
        )
        batch_world, batch_key, _, bills = prepared(conn, existing_run=w.run)
        convert(conn, batch_world, batch_key, bills)
    with pytest.raises(Exception, match="multi-bill conversion history must be retained"):
        command.downgrade(cfg, "0040")
