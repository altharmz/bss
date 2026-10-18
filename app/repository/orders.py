"""Product orders and their items (TMF622)."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.repository._sql import (
    Row,
    fetch_all,
    fetch_one,
    money,
    returning,
    select_page,
    update_by_id,
)


@dataclass(frozen=True)
class NewOrderItem:
    item_id: str  # TMF local id within the order, e.g. "1"
    action: str
    product_offering_id: UUID | None = None
    product_id: UUID | None = None
    state: str | None = "acknowledged"  # None: an item of a draft
    quoted_price_id: UUID | None = None
    quoted_price_name: str | None = None
    quoted_amount: Decimal | None = None
    quoted_currency: str | None = None
    parent_item_id: str | None = None  # the item_id it is nested in (listed before it)
    details: dict[str, Any] | None = None  # documents (migration 0026)


def _jsonb(value: dict[str, Any]) -> Jsonb:
    # tmfjson: numbers in a document stay exact decimals, never floats
    from app.api import tmfjson  # lazily: importing app.api loads every API module

    return Jsonb(value, dumps=tmfjson.dumps)


def create_order(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID | None,
    order_date: datetime,
    items: list[NewOrderItem],
    requested_start_date: date | None = None,
    state: str = "acknowledged",
    id: UUID | None = None,
    requested_initial_state: str | None = None,
    details: dict[str, Any] | None = None,
) -> Row:
    """Insert an order and its items, in the order given (`position`; a nested item after
    its parent). Returns the order row with an `items` list. `id` None: a new one (a
    client may choose it). The columns of migration 0026 are written only when given, so
    older schemas (tests/test_migrations.py) take the same call."""
    order = returning(
        conn,
        """
        INSERT INTO product_order
            (id, billing_account_id, state, order_date, requested_start_date)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s) RETURNING *
        """,
        (id, billing_account_id, state, order_date, requested_start_date),
    )
    given = {"requested_initial_state": requested_initial_state, "details": details}
    changes = {column: value for column, value in given.items() if value}
    if changes:
        if "details" in changes:
            changes["details"] = _jsonb(changes["details"])
        order = update_by_id(conn, "product_order", order["id"], changes)
    order["items"] = insert_items(conn, order["id"], items)
    return order


def insert_items(conn: psycopg.Connection, order_id: UUID, items: list[NewOrderItem]) -> list[Row]:
    rows: dict[str, Row] = {}
    for position, item in enumerate(items, start=1):
        row = returning(
            conn,
            """
            INSERT INTO product_order_item
                (product_order_id, item_id, position, action, state, product_offering_id,
                 product_id, quoted_price_id, quoted_price_name, quoted_amount,
                 quoted_currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                order_id,
                item.item_id,
                position,
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
        changes: dict[str, Any] = {}
        if item.parent_item_id is not None:
            changes["parent_id"] = rows[item.parent_item_id]["id"]
        if item.details:
            changes["details"] = _jsonb(item.details)
        if changes:
            row = update_by_id(conn, "product_order_item", row["id"], changes)
        rows[item.item_id] = row
    return list(rows.values())


def replace_draft(
    conn: psycopg.Connection,
    order_id: UUID,
    *,
    billing_account_id: UUID | None,
    requested_start_date: date | None,
    items: list[NewOrderItem] | None,
) -> None:
    """A draft's account, start date and (when given) items, replaced whole. A draft's
    items are referenced by nothing (never processed: no product, no message)."""
    conn.execute(
        """
        UPDATE product_order SET billing_account_id = %s, requested_start_date = %s
        WHERE id = %s AND state = 'draft'
        """,
        (billing_account_id, requested_start_date, order_id),
    )
    if items is not None:
        conn.execute("DELETE FROM product_order_item WHERE product_order_id = %s", (order_id,))
        insert_items(conn, order_id, items)


def submit_draft(
    conn: psycopg.Connection,
    order_id: UUID,
    *,
    billing_account_id: UUID,
    requested_start_date: date,
    items: list[NewOrderItem],
) -> Row:
    """A draft becomes `acknowledged`: its account and start date as processing derived
    them, its items in their initial states (same ids, order and documents). Returns the
    order row with an `items` list."""
    order = returning(
        conn,
        """
        UPDATE product_order
        SET state = 'acknowledged', billing_account_id = %s, requested_start_date = %s
        WHERE id = %s AND state = 'draft' RETURNING *
        """,
        (billing_account_id, requested_start_date, order_id),
    )
    conn.execute("DELETE FROM product_order_item WHERE product_order_id = %s", (order_id,))
    order["items"] = insert_items(conn, order_id, items)
    return order


def get_order(conn: psycopg.Connection, order_id: UUID) -> Row | None:
    order = fetch_one(conn, "SELECT * FROM product_order WHERE id = %s", (order_id,))
    if order is not None:
        order["items"] = fetch_all(
            conn,
            "SELECT * FROM product_order_item WHERE product_order_id = %s ORDER BY position",
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


def lock_order(conn: psycopg.Connection, order_id: UUID) -> Row | None:
    """The order row FOR UPDATE (a cancellation or delete of it), without items."""
    return fetch_one(conn, "SELECT * FROM product_order WHERE id = %s FOR UPDATE", (order_id,))


DESCRIPTIVE = ("description", "category", "priority", "external_id")


def set_attributes(conn: psycopg.Connection, order_id: UUID, changes: dict[str, Any]) -> Row:
    """The descriptive attributes (migration 0020) and the documents (`details`, 0026) -
    never anything processing decides."""
    assert set(changes) <= {*DESCRIPTIVE, "details"}, changes
    values = {
        k: Jsonb(v) if k == "external_id" else _jsonb(v) if k == "details" else v
        for k, v in changes.items()
    }
    return update_by_id(conn, "product_order", order_id, values)


def set_cancelled(
    conn: psycopg.Connection, order_id: UUID, *, at: datetime, reason: str | None
) -> Row:
    return returning(
        conn,
        """
        UPDATE product_order
        SET state = 'cancelled', cancellation_date = %s, cancellation_reason = %s
        WHERE id = %s RETURNING *
        """,
        (at, reason, order_id),
    )


def delete_unexecuted(conn: psycopg.Connection, order_id: UUID) -> None:
    """Remove a rejected or draft order with its items and reasons (it never executed)."""
    conn.execute("DELETE FROM product_order_error_message WHERE product_order_id = %s", (order_id,))
    conn.execute("DELETE FROM product_order_item WHERE product_order_id = %s", (order_id,))
    conn.execute(
        "DELETE FROM product_order WHERE id = %s AND state IN ('rejected', 'draft')", (order_id,)
    )


def insert_cancellation(
    conn: psycopg.Connection,
    *,
    order_id: UUID,
    at: datetime,
    requested: datetime | None,
    reason: str | None,
    id: UUID | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO cancel_product_order
            (id, product_order_id, creation_date, requested_cancellation_date,
             cancellation_reason)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s) RETURNING *
        """,
        (id, order_id, at, requested, reason),
    )


def get_cancellation(conn: psycopg.Connection, uid: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM cancel_product_order WHERE id = %s", (uid,))


def list_cancellations(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "cancel_product_order", where=where, offset=offset, limit=limit)


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
        "SELECT * FROM product_order_item WHERE product_order_id = %s ORDER BY position",
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
