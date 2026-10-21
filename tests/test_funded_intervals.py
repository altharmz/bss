"""Retained grant invariants without enabling prepaid posting or service reads."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors, sql
from psycopg.rows import dict_row

from alembic import command
from app.repository import prepaid_commands as commands
from app.repository._sql import jsonb
from tests.factories import NOW, World, account, charge, days, offering, product, rejected
from tests.test_prepaid_commands import enqueue

START, END = date(2026, 1, 15), date(2026, 2, 15)


def grant_world(conn, *, prepaid=True):
    acc = account(conn)
    if prepaid:
        conn.execute(
            "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],)
        )
    off = offering(conn)
    prod = product(conn, acc["id"], off["id"], START)
    cmd = enqueue(conn, acc)
    run = commands.ensure_run(conn, cmd["id"], run_date=START, now=NOW)
    return World(off, acc, prod, run)


def grant(conn, w, abr=None, **changes):
    abr = abr or charge(conn, w, days(START, END), "12.34")
    values = dict(
        product_id=w.product["id"],
        billing_account_id=w.account["id"],
        currency="USD",
        command_id=w.run["command_id"],
        command_kind="order",
        billing_run_id=w.run["id"],
        applied_rate_id=abr["id"],
        authorized_at=NOW,
        period=abr["period"],
        anchor_date=START,
        interval_index=0,
        amount=abr["amount"],
        tax_amount=abr["tax_amount"],
        quote=jsonb({"price": Decimal("12.34"), "tax": []}),
    )
    values.update(changes)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            sql.SQL("INSERT INTO funded_interval ({}) VALUES ({}) RETURNING *").format(
                sql.SQL(", ").join(map(sql.Identifier, values)),
                sql.SQL(", ").join(sql.Placeholder() for _ in values),
            ),
            tuple(values.values()),
        ).fetchone()


def test_retained_exact_grant_and_adjacent_renewal(db_conn):
    w = grant_world(db_conn)
    first = grant(db_conn, w)
    assert first["authorized_at"] == NOW
    assert first["period"] == days(START, END)
    assert first["amount"] == Decimal("12.34")
    cmd = enqueue(db_conn, w.account, kind="renewal")
    w.run = commands.ensure_run(db_conn, cmd["id"], run_date=END, now=NOW)
    abr = charge(db_conn, w, days(END, date(2026, 3, 15)), "0.00")
    second = grant(db_conn, w, abr, command_kind="renewal", interval_index=1)
    db_conn.commit()
    assert second["amount"] == 0
    assert second["period"].lower == first["period"].upper
    for statement in (
        "DELETE FROM funded_interval",
        "UPDATE funded_interval SET authorized_at = authorized_at + interval '1 hour'",
        "UPDATE funded_interval SET quote = '{}'::jsonb",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.ForeignKeyViolation):
        db_conn.execute("UPDATE applied_customer_billing_rate SET amount = amount + 1")


@pytest.mark.parametrize(
    "changes",
    [
        {"command_id": uuid4()},
        {"billing_run_id": uuid4()},
        {"billing_account_id": uuid4()},
        {"product_id": uuid4()},
        {"amount": Decimal("12.35")},
        {"tax_amount": Decimal("0.01")},
        {"currency": "GBP"},
        {"period": days(START, date(2026, 2, 16))},
        {"command_kind": "renewal"},
    ],
)
def test_grant_rejects_mismatched_financial_links(db_conn, changes):
    w = grant_world(db_conn)
    abr = charge(db_conn, w, days(START, END), "12.34")
    with rejected(db_conn, errors.ForeignKeyViolation):
        grant(db_conn, w, abr, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"period": days(START, START)},
        {"period": days(START, None)},
        {"anchor_date": END},
        {"interval_index": -1},
        {"amount": Decimal("NaN")},
        {"tax_amount": Decimal("-0.01")},
        {"quote": jsonb([])},
        {"command_kind": "transfer"},
    ],
)
def test_grant_rejects_invalid_coverage_and_quote(db_conn, changes):
    w = grant_world(db_conn)
    abr = charge(db_conn, w, days(START, END), "12.34")
    with rejected(db_conn, errors.CheckViolation):
        grant(db_conn, w, abr, **changes)


def test_postpaid_account_cannot_receive_grant(db_conn):
    w = grant_world(db_conn, prepaid=False)
    with rejected(db_conn, errors.ForeignKeyViolation):
        grant(db_conn, w)


def test_duplicate_command_and_anchor_are_refused(db_conn):
    w = grant_world(db_conn)
    first = grant(db_conn, w)
    abr = charge(db_conn, w, days(END, date(2026, 3, 15)), "12.34")
    with rejected(db_conn, errors.UniqueViolation):
        grant(db_conn, w, abr, interval_index=1)
    cmd = enqueue(db_conn, w.account, kind="renewal")
    run = commands.ensure_run(db_conn, cmd["id"], run_date=END, now=NOW)
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET billing_run_id = %s WHERE id = %s",
        (run["id"], abr["id"]),
    )
    with rejected(db_conn, errors.UniqueViolation):
        grant(
            db_conn, w, abr, command_id=cmd["id"], billing_run_id=run["id"], command_kind="renewal"
        )
    assert first["interval_index"] == 0


def test_grant_effects_rollback_together(db_conn):
    w = grant_world(db_conn)
    db_conn.commit()
    grant(db_conn, w)
    db_conn.rollback()
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone() == (0,)
    grant(db_conn, w)
    db_conn.commit()


def test_migration_preserves_finances_and_retains_grants(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0039")
    with psycopg.connect(temp_db_url) as conn:
        w = grant_world(conn)
        abr = charge(conn, w, days(START, END), "12.34")
    command.upgrade(cfg, "0040")
    command.downgrade(cfg, "0039")
    command.upgrade(cfg, "0040")
    with psycopg.connect(temp_db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            assert cur.execute("SELECT * FROM applied_customer_billing_rate").fetchone() == abr
        grant(conn, w, abr)
    with pytest.raises(Exception, match="funded interval history must be retained"):
        command.downgrade(cfg, "0039")
