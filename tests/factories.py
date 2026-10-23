"""Builders for a minimal, valid object graph. Each returns the inserted row(s)."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.types.range import Range

from app.repository import billing, catalog, orders, parties, products
from app.repository._sql import Row

MSK = ZoneInfo("Europe/Moscow")
T0 = datetime(2026, 1, 1, tzinfo=MSK)
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=MSK)


@contextmanager
def rejected(conn: psycopg.Connection, error: type[Exception]):
    """Assert the block fails with `error`, keeping the outer transaction usable."""
    with pytest.raises(error), conn.transaction():
        yield


def offering(conn: psycopg.Connection) -> Row:
    spec = catalog.create_product_specification(conn, name="Fiber", lifecycle_status="Active")
    return catalog.create_product_offering(
        conn,
        product_specification_id=spec["id"],
        name="Fiber 100",
        lifecycle_status="Active",
        valid_for=Range(T0, None, "[)"),
    )


def account(conn: psycopg.Connection, currency: str = "USD", name: str = "Ivanov") -> Row:
    party = parties.create_individual(conn, given_name="Ivan", family_name=name)
    customer = parties.create_customer(conn, party_id=party["id"], name=name)
    return parties.create_billing_account(
        conn,
        customer_id=customer["id"],
        name=f"{name} main",
        currency=currency,
        bill_cycle_day=1,
        opened_at=T0,
    )


def add_order(conn: psycopg.Connection, account_id, offering_id) -> Row:
    """An order with a single 'add' item."""
    return orders.create_order(
        conn,
        billing_account_id=account_id,
        order_date=NOW,
        items=[orders.NewOrderItem(item_id="1", action="add", product_offering_id=offering_id)],
    )


def product(conn: psycopg.Connection, account_id, offering_id, start: date) -> Row:
    order = add_order(conn, account_id, offering_id)
    return products.create_product(
        conn,
        billing_account_id=account_id,
        product_offering_id=offering_id,
        created_by_item_id=order["items"][0]["id"],
        start_date=start,
    )


def run(conn: psycopg.Connection, run_date: date = date(2026, 2, 1)) -> Row:
    return billing.insert_run(conn, run_date, now=NOW, trigger="scheduled")


@dataclass
class World:
    offering: Row
    account: Row
    product: Row
    run: Row


def world(conn: psycopg.Connection) -> World:
    off = offering(conn)
    acc = account(conn)
    prod = product(conn, acc["id"], off["id"], date(2026, 1, 1))
    return World(offering=off, account=acc, product=prod, run=run(conn))


def charge(
    conn: psycopg.Connection,
    w: World,
    period: Range,
    amount: str = "100.00",
    product_id=None,
) -> Row:
    return billing.insert_abr(
        conn,
        product_id=product_id or w.product["id"],
        billing_account_id=w.account["id"],
        charge_type="recurring",
        amount=Decimal(amount),
        currency=w.account["currency"],
        period=period,
        billing_run_id=w.run["id"],
        applied_at=w.run["started_at"],
    )


def days(start: date, end: date) -> Range:
    return Range(start, end, "[)")


def prepaid_bucket(conn: psycopg.Connection, account_id, at: datetime) -> Row:
    """Make a history-free account prepaid with its one bucket (prepaid creation through
    the API waits for P5a)."""
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (account_id,))
    row = conn.execute(
        "INSERT INTO prepaid_bucket (billing_account_id, currency, created_at)"
        " SELECT id, currency, %s FROM billing_account WHERE id = %s"
        " RETURNING id, accumulated_balance_id",
        (at, account_id),
    ).fetchone()
    return {"id": row[0], "accumulated_balance_id": row[1]}


def payment_hold(conn: psycopg.Connection, account_id, payment_id, amount, at: datetime):
    """An outstanding hold of `amount` on one payment."""
    hold = conn.execute(
        "INSERT INTO funding_hold (billing_account_id, currency, amount, held_at)"
        " SELECT id, currency, %s, %s FROM billing_account WHERE id = %s RETURNING id",
        (amount, at, account_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO funding_hold_source (hold_id, billing_account_id, payment_id, amount)"
        " VALUES (%s, %s, %s, %s)",
        (hold, account_id, payment_id, amount),
    )
    return hold
