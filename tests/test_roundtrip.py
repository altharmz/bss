"""Values survive the trip to Postgres and back with their Python types intact."""

from datetime import date
from decimal import Decimal

import pytest
from psycopg.types.range import Range

from app.repository import billing, catalog, payments
from tests.factories import NOW, T0, account, offering


@pytest.mark.parametrize("amount", ["1234567890.12", "0.01", "9999999999.99"])
def test_decimal_roundtrip(db_conn, amount):
    acc = account(db_conn)
    off = offering(db_conn)
    price = catalog.create_product_offering_price(
        db_conn,
        product_offering_id=off["id"],
        price_type="oneTime",
        amount=Decimal(amount),
        currency="RUB",
        valid_for=Range(T0, None, "[)"),
    )
    pay = payments.insert_payment(
        db_conn,
        billing_account_id=acc["id"],
        amount=Decimal(amount),
        currency="RUB",
        payment_method="bankTransfer",
        received_at=NOW,
    )
    for stored in (
        catalog.get_product_offering_price(db_conn, price["id"])["amount"],
        pay["amount"],
    ):
        assert isinstance(stored, Decimal)
        assert stored == Decimal(amount)


def test_float_money_is_refused(db_conn):
    acc = account(db_conn)
    with pytest.raises(TypeError):
        payments.insert_payment(
            db_conn,
            billing_account_id=acc["id"],
            amount=0.1,
            currency="RUB",
            payment_method="bankTransfer",
            received_at=NOW,
        )


@pytest.mark.parametrize("upper", [NOW, None], ids=["bounded", "open-ended"])
def test_tstzrange_roundtrip(db_conn, upper):
    valid_for = Range(T0, upper, "[)")
    spec = catalog.create_product_specification(db_conn, name="s", lifecycle_status="In Design")
    off = catalog.create_product_offering(
        db_conn,
        product_specification_id=spec["id"],
        name="o",
        lifecycle_status="In Design",
        valid_for=valid_for,
    )
    stored = catalog.get_product_offering(db_conn, off["id"])["valid_for"]
    assert isinstance(stored, Range)
    assert stored == valid_for
    assert stored.lower_inc and not stored.upper_inc
    assert stored.upper == upper


def test_daterange_roundtrip(db_conn):
    acc = account(db_conn)
    period = Range(date(2026, 1, 1), date(2026, 2, 1), "[)")
    run = billing.insert_run(db_conn, date(2026, 2, 1), now=NOW, trigger="scheduled")
    bill = billing.insert_bill(
        db_conn,
        billing_account_id=acc["id"],
        currency="RUB",
        bill_period=period,
        bill_date=date(2026, 2, 1),
        amount_due=Decimal("0.00"),
        billing_run_id=run["id"],
    )
    stored = db_conn.execute(
        "SELECT bill_period FROM customer_bill WHERE id = %s", (bill["id"],)
    ).fetchone()[0]
    assert isinstance(stored, Range)
    assert stored == period
    assert isinstance(stored.lower, date)
