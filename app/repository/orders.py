"""Product orders and their items (TMF622)."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning


@dataclass(frozen=True)
class NewOrderItem:
    item_id: str  # TMF local id within the order, e.g. "1"
    action: str
    product_offering_id: UUID | None = None
    product_id: UUID | None = None
    state: str = "acknowledged"


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
                (product_order_id, item_id, action, state, product_offering_id, product_id)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                order["id"],
                item.item_id,
                item.action,
                item.state,
                item.product_offering_id,
                item.product_id,
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
