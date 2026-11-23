"""Number persistence guarantees, independent of the future inventory/order routes."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors, sql

from alembic import command
from app.repository import billing, payments
from tests.builders.number_persistence import assignment, number
from tests.factories import NOW, charge, days, product, rejected, world


@pytest.mark.migration_history
def test_number_requirement_migration_preserves_existing_products(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0032")
    with psycopg.connect(temp_db_url) as conn:
        w = world(conn)
        before = conn.execute("SELECT row_to_json(product) FROM product").fetchone()[0]
    command.upgrade(cfg, "0033")
    with psycopg.connect(temp_db_url) as conn:
        after = conn.execute("SELECT row_to_json(product) FROM product").fetchone()[0]
        assert after.pop("requires_msisdn") is False
        assert after == before
        conn.execute("UPDATE product SET requires_msisdn = true WHERE id = %s", (w.product["id"],))
    with pytest.raises(Exception, match="number-requiring products"):
        command.downgrade(cfg, "0032")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("UPDATE product SET requires_msisdn = false")
    command.downgrade(cfg, "0032")


@pytest.mark.parametrize(
    "value",
    [
        "+12025550123",
        "120 25550123",
        "012345678",
        "1234567",
        "1" * 16,
        "１２３４５６７８",
        "12025550123\n",
    ],
)
def test_number_requires_canonical_e164_digits(db_conn, value):
    with rejected(db_conn, errors.CheckViolation):
        number(db_conn, value)


def test_number_uniqueness_fixed_specification_and_documents(db_conn):
    for value in ("12345678", "1" * 15):
        number(db_conn, value)
    with rejected(db_conn, errors.UniqueViolation):
        number(db_conn, "12345678")
    for clause in ("resource_specification_id = 'other'", "attrs = '[]'", "tmf_meta = 'null'"):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(sql.SQL("UPDATE subscriber_number SET {} ").format(sql.SQL(clause)))
    with rejected(db_conn, errors.InvalidTextRepresentation):
        db_conn.execute("UPDATE subscriber_number SET administrative_state = 'unknown'")


def test_future_and_current_claims_exclude_number_and_product_overlaps(db_conn):
    w = world(db_conn)
    other = product(db_conn, w.account["id"], w.offering["id"], date(2026, 1, 1))
    first, second = number(db_conn), number(db_conn, "12025550124")
    assignment(db_conn, first, w.product, start=date(2027, 1, 1))
    with rejected(db_conn, errors.ExclusionViolation):
        assignment(db_conn, first, other)  # current open-ended claim conflicts with future
    with rejected(db_conn, errors.ExclusionViolation):
        assignment(db_conn, second, w.product, start=date(2027, 2, 1))
    assignment(db_conn, first, other, end=date(2027, 1, 1))  # adjacent release is valid


def test_cancelled_and_zero_length_claims_retain_audit_without_claiming_dates(db_conn):
    w = world(db_conn)
    n = number(db_conn)
    cancelled = assignment(db_conn, n, w.product, cancelled=True)
    assignment(db_conn, n, w.product, end=date(2026, 1, 1))
    assignment(db_conn, n, w.product)
    with rejected(db_conn, errors.ExclusionViolation):
        db_conn.execute(
            "UPDATE number_assignment SET cancelled_at = NULL, cancelled_by_item_id = NULL"
            " WHERE id = %s",
            (cancelled,),
        )
    assert db_conn.execute(
        "SELECT count(*), count(DISTINCT audit_sequence), count(*) FILTER"
        " (WHERE isempty(valid_for)) FROM number_assignment"
    ).fetchone() == (3, 3, 1)
    for table, key in (("subscriber_number", n), ("product", w.product["id"])):
        with rejected(db_conn, errors.ForeignKeyViolation):
            db_conn.execute(
                sql.SQL("DELETE FROM {} WHERE id = %s").format(sql.Identifier(table)), (key,)
            )


@pytest.mark.parametrize(
    "clause, error",
    [
        ("quarantine_days = -1", errors.CheckViolation),
        ("effective_from = 'infinity'", errors.CheckViolation),
        ("released_on = '2025-12-31'", errors.DataException),
        ("released_on = '2026-01-02'", errors.CheckViolation),
        ("cancelled_at = now()", errors.CheckViolation),
        ("subscriber_number_id = %s", errors.ForeignKeyViolation),
        ("assigned_by_item_id = %s", errors.ForeignKeyViolation),
    ],
)
def test_assignment_rejects_invalid_transitions_and_references(db_conn, clause, error):
    w = world(db_conn)
    assignment(db_conn, number(db_conn), w.product)
    with rejected(db_conn, error):
        db_conn.execute(
            sql.SQL("UPDATE number_assignment SET {}").format(sql.SQL(clause)),
            (uuid4(),) if "%s" in clause else None,
        )


@pytest.mark.migration_history
def test_number_migration_preserves_financial_history_and_blocks_lossy_downgrade(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0031")
    with psycopg.connect(temp_db_url) as conn:
        w = world(conn)
        charge(conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
        payments.insert_payment(
            conn,
            billing_account_id=w.account["id"],
            currency="USD",
            amount=Decimal("200"),
            received_at=NOW,
            payment_method="cash",
        )
        conn.execute(
            "WITH payout AS ("
            " INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_name)"
            " VALUES (%s, 'USD', 10, %s, 'cash') RETURNING id"
            ") INSERT INTO refund_source (refund_id, billing_account_id, payment_id, amount)"
            " SELECT payout.id, p.billing_account_id, p.id, 10 FROM payout, payment p",
            (w.account["id"], NOW),
        )
        billing.insert_bill(
            conn,
            billing_account_id=w.account["id"],
            currency="USD",
            bill_period=days(date(2026, 1, 1), date(2026, 2, 1)),
            bill_date=date(2026, 2, 1),
            amount_due=Decimal("100"),
            billing_run_id=w.run["id"],
            payment_due_date=date(2026, 3, 1),
        )
        before = {
            table: conn.execute(
                sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table))
            ).fetchall()
            for table in (
                "billing_account",
                "product",
                "applied_customer_billing_rate",
                "billing_run",
                "payment",
                "refund",
                "refund_source",
                "customer_bill",
            )
        }
    command.upgrade(cfg, "0032")
    with psycopg.connect(temp_db_url) as conn:
        for table, rows in before.items():
            assert (
                conn.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table))
                ).fetchall()
                == rows
            )
        with rejected(conn, errors.CheckViolation):
            conn.execute("UPDATE refund SET amount = amount + 1")
        number(conn)
    with pytest.raises(Exception, match="inventory/history must be removed"):
        command.downgrade(cfg, "0031")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0032",)
        assignment(conn, conn.execute("SELECT id FROM subscriber_number").fetchone()[0], w.product)
    with pytest.raises(Exception, match="inventory/history must be removed"):
        command.downgrade(cfg, "0031")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM number_assignment").fetchone() == (1,)
        conn.execute("DELETE FROM number_assignment")
        conn.execute("DELETE FROM subscriber_number")
    command.downgrade(cfg, "0031")
    command.upgrade(cfg, "0032")
