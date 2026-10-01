"""Product inventory (TMF637). Products are only ever created or terminated by an order
item - hence the mandatory `created_by_item_id` / `terminated_by_item_id`."""

from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning, select_page


def create_product(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID,
    product_offering_id: UUID,
    created_by_item_id: UUID,
    start_date: date,
    status: str = "active",
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product
            (billing_account_id, product_offering_id, status, start_date, created_by_item_id)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (billing_account_id, product_offering_id, status, start_date, created_by_item_id),
    )


def get_product(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product WHERE id = %s", (product_id,))


def set_product_status(conn: psycopg.Connection, product_id: UUID, status: str) -> Row:
    return returning(
        conn, "UPDATE product SET status = %s WHERE id = %s RETURNING *", (status, product_id)
    )


def terminate_product(
    conn: psycopg.Connection,
    product_id: UUID,
    *,
    termination_date: date,
    terminated_by_item_id: UUID,
    status: str = "terminated",
) -> Row:
    """`termination_date` is exclusive: the product is no longer billable on that day."""
    return returning(
        conn,
        """
        UPDATE product
        SET status = %s, termination_date = %s, terminated_by_item_id = %s
        WHERE id = %s RETURNING *
        """,
        (status, termination_date, terminated_by_item_id, product_id),
    )


def list_active_products(conn: psycopg.Connection, billing_account_id: UUID, on: date) -> list[Row]:
    """Products of the account that are billable on `on`.

    Driven by dates, not by lifecycle status: a future-dated product is billable from its
    start day whether or not activation has run yet. Only products that never came into
    being (cancelled, aborted) are excluded by status.
    """
    return fetch_all(
        conn,
        """
        SELECT * FROM product
        WHERE billing_account_id = %s
          AND start_date <= %s
          AND (termination_date IS NULL OR %s < termination_date)
          AND status NOT IN ('cancelled', 'aborted')
        ORDER BY start_date, id
        """,
        (billing_account_id, on, on),
    )


def list_products(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "product", where=where, offset=offset, limit=limit)


def lock_products(conn: psycopg.Connection, product_ids: list[UUID]) -> dict[UUID, Row]:
    """Lock these product rows until the transaction ends (SELECT ... FOR UPDATE), in id
    order so two orders locking overlapping sets cannot deadlock. A concurrent order that
    wants the same products waits here, then sees their committed state."""
    rows = fetch_all(
        conn,
        "SELECT * FROM product WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        (sorted(set(product_ids)),),
    )
    return {row["id"]: row for row in rows}


# Products that are still to be billed or are being billed: not ended. What the admin
# UI lists as an account's products.
OPEN_STATUSES = ("created", "pendingActive", "active", "pendingTerminate", "suspended")


def list_open_products(conn: psycopg.Connection, billing_account_id: UUID) -> list[Row]:
    """The account's open products with their offering (name, code) and specification
    (name), oldest start first."""
    return fetch_all(
        conn,
        """
        SELECT p.*, o.name AS offering_name, o.code AS offering_code,
               s.id AS specification_id, s.name AS specification_name
        FROM product p
        JOIN product_offering o ON o.id = p.product_offering_id
        JOIN product_specification s ON s.id = o.product_specification_id
        WHERE p.billing_account_id = %s AND p.status = ANY(%s)
        ORDER BY p.start_date, p.created_at, p.id
        """,
        (billing_account_id, list(OPEN_STATUSES)),
    )
