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


def account(conn: psycopg.Connection, currency: str = "RUB", name: str = "Ivanov") -> Row:
    party = parties.create_individual(conn, given_name="Ivan", family_name=name)
    customer = parties.create_customer(conn, party_id=party["id"])
    return parties.create_billing_account(
        conn, customer_id=customer["id"], name=f"{name} main", currency=currency, bill_cycle_day=1
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
    return billing.start_run(conn, run_date, now=NOW)


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
    )


def days(start: date, end: date) -> Range:
    return Range(start, end, "[)")
