"""Builders other test modules share, from tests/prepaid/test_prepaid_renewal.py (#423)."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from psycopg.types.range import Range

from app.repository import catalog
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests.builders.prepaid.prepaid_posting import post, prepared
from tests.factories import NOW, T0


def renewal(conn, *, amount="5", unit="monthly", length=1, delay=0):
    acc, prod, cmd, _ = prepared(conn)
    assert worker_db.acquire_ownership(conn)
    first = post(conn, cmd)
    due = NOW.replace(month=2)
    price = catalog.create_product_offering_price(
        conn,
        product_offering_id=prod["product_offering_id"],
        name="Current",
        price_type="recurring",
        amount=D(amount),
        currency="USD",
        valid_for=Range(T0, None, "[)"),
        recurring_period=unit,
        recurring_period_length=length,
        details={"tax": [{"taxCategory": "VAT", "taxRate": 20}]},
    )
    cmd = commands.enqueue(
        conn,
        uuid4(),
        acc["id"],
        kind="renewal",
        due_date=due.date(),
        request={"previous_interval_id": first["interval_id"]},
        now=due,
    )
    return acc, cmd, price, due + timedelta(days=delay)
