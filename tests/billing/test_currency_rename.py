"""The currency rename preserves amounts, links, documents and other currencies."""

import base64
import json
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from alembic.config import Config

from alembic import command
from app.repository import billing, payments
from tests import factories


@pytest.mark.migration_history
def test_0031_relabels_existing_money_atomically(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0030")
    with psycopg.connect(temp_db_url) as conn:
        account = factories.account(conn, currency="RUB")
        offering = factories.offering(conn)
        product = factories.product(conn, account["id"], offering["id"], date(2026, 1, 1))
        world = factories.World(offering, account, product, factories.run(conn))
        factories.charge(conn, world, factories.days(date(2026, 1, 1), date(2026, 2, 1)))
        factories.account(conn, currency="EUR")
        payments.insert_payment(
            conn,
            billing_account_id=account["id"],
            currency="RUB",
            amount=Decimal("20.00"),
            received_at=factories.NOW,
            payment_method="cash",
        )
        conn.execute(
            "WITH payout AS ("
            " INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_name)"
            " VALUES (%s, 'RUB', 10, now(), 'cash') RETURNING id"
            ") INSERT INTO refund_source (refund_id, billing_account_id, payment_id, amount)"
            " SELECT payout.id, p.billing_account_id, p.id, 10 FROM payout, payment p",
            (account["id"],),
        )
        bill = billing.insert_bill(
            conn,
            billing_account_id=account["id"],
            currency="RUB",
            bill_period=factories.days(date(2026, 1, 1), date(2026, 2, 1)),
            bill_date=date(2026, 2, 1),
            amount_due=Decimal("100.00"),
            billing_run_id=world.run["id"],
            payment_due_date=date(2026, 3, 1),
        )
        conn.execute(
            "UPDATE customer_bill SET bill_document = %s WHERE id = %s",
            (base64.b64encode(b'{"amountDue":{"unit":"RUB","value":100.00}}').decode(), bill["id"]),
        )
        conn.execute("""
            UPDATE billing_account SET details =
            '{"paymentPlan":[{"totalAmount":{"unit":"RUB","value":123.45}}],
              "description":"RUB", "other":{"unit":"EUR","value":5}}'
            WHERE currency = 'RUB'
        """)
        conn.execute("""
            WITH price AS (
                INSERT INTO product_offering_price
                    (name, lifecycle_status, price_type, amount, currency, valid_for)
                VALUES ('Original', 'Active', 'oneTime', 100, 'RUB', tstzrange(now(), NULL))
                RETURNING id
            )
            UPDATE product_order_item SET quoted_currency = 'RUB', quoted_amount = 100,
                quoted_price_id = (SELECT id FROM price), quoted_price_name = 'Original'
        """)
        before = conn.execute("""
            SELECT conname, condeferrable, condeferred FROM pg_constraint
            WHERE contype = 'f' ORDER BY conname
        """).fetchall()
    # Compare FK timing across this migration alone; later migrations may add FKs.
    command.upgrade(cfg, "0031")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT currency FROM billing_account ORDER BY currency"
        ).fetchall() == [("EUR",), ("USD",)]
        assert conn.execute(
            "SELECT currency, amount FROM applied_customer_billing_rate"
        ).fetchone() == ("USD", Decimal("100.00"))
        assert conn.execute(
            "SELECT quoted_currency, quoted_amount FROM product_order_item"
        ).fetchone() == ("USD", Decimal("100.00"))
        details = conn.execute(
            "SELECT details FROM billing_account WHERE id = %s", (account["id"],)
        ).fetchone()[0]
        assert details["paymentPlan"][0]["totalAmount"] == {"unit": "USD", "value": 123.45}
        assert details["description"] == "RUB"
        assert details["other"] == {"unit": "EUR", "value": 5}
        assert conn.execute("SELECT currency, amount FROM refund").fetchone() == (
            "USD",
            Decimal("10.00"),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="immutable"), conn.transaction():
            conn.execute("UPDATE refund SET amount = amount + 1")
        document = conn.execute("SELECT bill_document FROM customer_bill").fetchone()[0]
        assert json.loads(base64.b64decode(document), parse_float=Decimal) == {
            "amountDue": {"unit": "USD", "value": Decimal("100.00")}
        }
        assert (
            conn.execute("""
            SELECT conname, condeferrable, condeferred FROM pg_constraint
            WHERE contype = 'f' ORDER BY conname
        """).fetchall()
            == before
        )
    command.downgrade(cfg, "0030")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute(
            "SELECT currency FROM billing_account WHERE id = %s", (account["id"],)
        ).fetchone() == ("USD",)
