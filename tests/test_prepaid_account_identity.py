"""Prepaid identity foundation; public creation remains unavailable."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors, sql

from alembic import command
from app.repository import payments
from tests.factories import NOW, account, charge, days, rejected, world


def bucket(conn, acc, currency="USD"):
    return conn.execute(
        "INSERT INTO prepaid_bucket (billing_account_id, currency, created_at)"
        " VALUES (%s, %s, %s) RETURNING id, accumulated_balance_id",
        (acc["id"], currency, NOW),
    ).fetchone()


def test_account_mode_defaults_and_prepaid_credit_limit(db_conn):
    acc = account(db_conn)
    assert acc["rating_type"] == "postpaid"
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_account SET rating_type = 'unknown'")
    db_conn.execute("UPDATE billing_account SET credit_limit = 10")
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")
    db_conn.execute("UPDATE billing_account SET credit_limit = 0, rating_type = 'prepaid'")
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_account SET credit_limit = 1")
    db_conn.execute("UPDATE billing_account SET credit_limit = NULL")
    # An unused account can still switch back before any bucket or money history.
    db_conn.execute("UPDATE billing_account SET rating_type = 'postpaid'")


def test_bucket_requires_prepaid_owner_and_matching_currency(db_conn):
    acc = account(db_conn)
    with rejected(db_conn, errors.ForeignKeyViolation):
        bucket(db_conn, acc)
    db_conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")
    with rejected(db_conn, errors.ForeignKeyViolation):
        bucket(db_conn, acc, "GBP")
    with rejected(db_conn, errors.ForeignKeyViolation):
        bucket(db_conn, {"id": uuid4()})
    identity = bucket(db_conn, acc)
    assert identity[0] != identity[1]
    with rejected(db_conn, errors.UniqueViolation):
        bucket(db_conn, acc)
    assert (
        db_conn.execute(
            "SELECT id, accumulated_balance_id FROM prepaid_bucket WHERE billing_account_id = %s",
            (acc["id"],),
        ).fetchone()
        == identity
    )
    with rejected(db_conn, errors.ForeignKeyViolation):
        db_conn.execute("DELETE FROM billing_account WHERE id = %s", (acc["id"],))
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_account SET rating_type = 'postpaid'")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE prepaid_bucket SET id = gen_random_uuid()",
        "UPDATE prepaid_bucket SET accumulated_balance_id = gen_random_uuid()",
        "UPDATE prepaid_bucket SET currency = 'GBP'",
        "UPDATE prepaid_bucket SET created_at = created_at + interval '1 day'",
        "DELETE FROM prepaid_bucket",
    ],
)
def test_bucket_identity_cannot_be_rewritten_or_deleted(db_conn, statement):
    acc = account(db_conn)
    db_conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")
    bucket(db_conn, acc)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(statement)


@pytest.mark.parametrize("history", ["order", "payment", "run"])
def test_account_mode_cannot_change_after_history(db_conn, history):
    acc = account(db_conn)
    if history == "payment":
        payments.insert_payment(
            db_conn,
            billing_account_id=acc["id"],
            currency="USD",
            amount=Decimal("10"),
            received_at=NOW,
            payment_method="cash",
        )
    elif history == "order":
        from tests.factories import add_order, offering

        add_order(db_conn, acc["id"], offering(db_conn)["id"])
    else:
        from tests.factories import run

        db_conn.execute(
            "INSERT INTO billing_run_account (run_id, billing_account_id, status, finished_at)"
            " VALUES (%s, %s, 'ok', %s)",
            (run(db_conn)["id"], acc["id"], NOW),
        )
    # Repeated mode writes are harmless even with history.
    db_conn.execute("UPDATE billing_account SET rating_type = 'postpaid'")
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")


def test_migration_preserves_populated_finances_and_refund_immutability(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0033")
    tables = (
        "billing_account",
        "product",
        "product_order",
        "applied_customer_billing_rate",
        "payment",
        "refund",
        "refund_source",
        "billing_run",
    )
    with psycopg.connect(temp_db_url) as conn:
        w = world(conn)
        charge(conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
        payment = payments.insert_payment(
            conn,
            billing_account_id=w.account["id"],
            currency="USD",
            amount=Decimal("200"),
            received_at=NOW,
            payment_method="cash",
        )
        conn.execute(
            "WITH payout AS (INSERT INTO refund"
            " (billing_account_id, currency, amount, refunded_at, method_name)"
            " VALUES (%s, 'USD', 10, %s, 'cash') RETURNING id)"
            " INSERT INTO refund_source (refund_id, billing_account_id, payment_id, amount)"
            " SELECT id, %s, %s, 10 FROM payout",
            (w.account["id"], NOW, w.account["id"], payment["id"]),
        )
        before = {
            table: conn.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY id").format(sql.Identifier(table))
            ).fetchall()
            for table in tables
        }
    command.upgrade(cfg, "0034")
    with psycopg.connect(temp_db_url) as conn:
        for table, rows in before.items():
            after = conn.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY id").format(sql.Identifier(table))
            ).fetchall()
            if table == "billing_account":
                assert all(row[0].pop("rating_type") == "postpaid" for row in after)
            assert after == rows, table
        assert conn.execute("SELECT count(*) FROM prepaid_bucket").fetchone() == (0,)
        with rejected(conn, errors.CheckViolation):
            conn.execute("UPDATE refund SET amount = amount + 1")
    command.downgrade(cfg, "0033")
    command.upgrade(cfg, "0034")


def test_downgrade_refuses_prepaid_mode_and_retained_bucket(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0034")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")
    with pytest.raises(Exception, match="prepaid account/bucket history must be retained"):
        command.downgrade(cfg, "0033")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0034",)
        bucket(conn, acc)
    with pytest.raises(Exception, match="prepaid account/bucket history must be retained"):
        command.downgrade(cfg, "0033")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM prepaid_bucket").fetchone() == (1,)
