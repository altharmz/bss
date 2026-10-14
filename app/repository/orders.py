"""Product orders and their items (TMF622)."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, money, returning, select_page


@dataclass(frozen=True)
class NewOrderItem:
    item_id: str  # TMF local id within the order, e.g. "1"
    action: str
    product_offering_id: UUID | None = None
    product_id: UUID | None = None
    state: str = "acknowledged"
    quoted_price_id: UUID | None = None
    quoted_price_name: str | None = None
    quoted_amount: Decimal | None = None
    quoted_currency: str | None = None


def create_order(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID,
    order_date: datetime,
    items: list[NewOrderItem],
    requested_start_date: date | None = None,
    state: str = "acknowledged",
) -> Row:
    """Insert an order and its items. Returns the order row with an `items` list."""
    order = returning(
        conn,
        """
        INSERT INTO product_order (billing_account_id, state, order_date, requested_start_date)
        VALUES (%s, %s, %s, %s) RETURNING *
        """,
        (billing_account_id, state, order_date, requested_start_date),
    )
    order["items"] = [
        returning(
            conn,
            """
            INSERT INTO product_order_item
                (product_order_id, item_id, action, state, product_offering_id, product_id,
                 quoted_price_id, quoted_price_name, quoted_amount, quoted_currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                order["id"],
                item.item_id,
                item.action,
                item.state,
                item.product_offering_id,
                item.product_id,
                item.quoted_price_id,
                item.quoted_price_name,
                money(item.quoted_amount) if item.quoted_amount is not None else None,
                item.quoted_currency,
            ),
        )
        for item in items
    ]
    return order


def get_order(conn: psycopg.Connection, order_id: UUID) -> Row | None:
    order = fetch_one(conn, "SELECT * FROM product_order WHERE id = %s", (order_id,))
    if order is not None:
        order["items"] = fetch_all(
            conn,
            "SELECT * FROM product_order_item WHERE product_order_id = %s ORDER BY item_id",
            (order_id,),
        )
    return order


def set_order_state(
    conn: psycopg.Connection,
    order_id: UUID,
    state: str,
    *,
    completion_date: datetime | None = None,
) -> Row:
    """Change the order state; `completion_date`, when given, is recorded too."""
    return returning(
        conn,
        """
        UPDATE product_order
        SET state = %s, completion_date = coalesce(%s, completion_date)
        WHERE id = %s RETURNING *
        """,
        (state, completion_date, order_id),
    )


def set_order_item_state(conn: psycopg.Connection, order_item_id: UUID, state: str) -> Row:
    return returning(
        conn,
        "UPDATE product_order_item SET state = %s WHERE id = %s RETURNING *",
        (state, order_item_id),
    )


def get_order_item(conn: psycopg.Connection, order_item_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_order_item WHERE id = %s", (order_item_id,))


def list_orders(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    """A page of orders (without items; see `items_of`)."""
    return select_page(conn, "product_order", where=where, offset=offset, limit=limit)


def items_of(conn: psycopg.Connection, order_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM product_order_item WHERE product_order_id = %s ORDER BY item_id",
        (order_id,),
    )


def set_order_item_product(conn: psycopg.Connection, order_item_id: UUID, product_id: UUID) -> Row:
    """Record the product an `add` item created."""
    return returning(
        conn,
        "UPDATE product_order_item SET product_id = %s WHERE id = %s RETURNING *",
        (product_id, order_item_id),
    )


def open_delete_items(conn: psycopg.Connection, product_id: UUID) -> list[Row]:
    """Delete items for this product that are not finished yet (at most one exists: the
    product_order_item_one_open_delete index)."""
    return fetch_all(
        conn,
        """
        SELECT * FROM product_order_item
        WHERE product_id = %s AND action = 'delete'
          AND state NOT IN ('completed', 'cancelled', 'rejected', 'failed')
        """,
        (product_id,),
    )


def add_error_message(
    conn: psycopg.Connection,
    *,
    order_id: UUID,
    order_item_id: UUID | None,
    code: str,
    reason: str,
    occurred_at: datetime,
    message: str | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product_order_error_message
            (product_order_id, product_order_item_id, code, reason, message, occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (order_id, order_item_id, code, reason, message, occurred_at),
    )


def error_messages(conn: psycopg.Connection, order_id: UUID) -> list[Row]:
    """Order-level messages first, then per item, alphabetically by code."""
    return fetch_all(
        conn,
        """
        SELECT * FROM product_order_error_message WHERE product_order_id = %s
        ORDER BY product_order_item_id NULLS FIRST, occurred_at, code, id
        """,
        (order_id,),
    )
