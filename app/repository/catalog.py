"""Product catalog (TMF620): specifications, offerings, offering prices."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.range import Range

from app.repository._sql import Row, fetch_one, money, returning


def create_product_specification(
    conn: psycopg.Connection,
    *,
    name: str,
    lifecycle_status: str,
    description: str | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product_specification (name, description, lifecycle_status)
        VALUES (%s, %s, %s) RETURNING *
        """,
        (name, description, lifecycle_status),
    )


def get_product_specification(conn: psycopg.Connection, spec_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_specification WHERE id = %s", (spec_id,))


def create_product_offering(
    conn: psycopg.Connection,
    *,
    product_specification_id: UUID,
    name: str,
    lifecycle_status: str,
    valid_for: Range,
    is_sellable: bool = True,
    description: str | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product_offering
            (product_specification_id, name, description, lifecycle_status,
             is_sellable, valid_for)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (product_specification_id, name, description, lifecycle_status, is_sellable, valid_for),
    )


def get_product_offering(conn: psycopg.Connection, offering_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_offering WHERE id = %s", (offering_id,))


def create_product_offering_price(
    conn: psycopg.Connection,
    *,
    product_offering_id: UUID,
    price_type: str,
    amount: Decimal,
    currency: str,
    valid_for: Range,
    recurring_period: str | None = None,
    name: str | None = None,
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product_offering_price
            (product_offering_id, name, price_type, recurring_period, amount, currency,
             valid_for)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            product_offering_id,
            name,
            price_type,
            recurring_period,
            money(amount),
            currency,
            valid_for,
        ),
    )


def get_product_offering_price(conn: psycopg.Connection, price_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_offering_price WHERE id = %s", (price_id,))


def price_at(
    conn: psycopg.Connection,
    *,
    product_offering_id: UUID,
    price_type: str,
    currency: str,
    at: datetime,
) -> Row | None:
    """The price of this type and currency valid for the offering at instant `at`.

    At most one can match: the EXCLUDE constraint forbids overlapping validity. To price a
    billing date, pass midnight of that date in BILLING_TZ.
    """
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware")
    return fetch_one(
        conn,
        """
        SELECT * FROM product_offering_price
        WHERE product_offering_id = %s AND price_type = %s AND currency = %s
          AND valid_for @> %s::timestamptz
        """,
        (product_offering_id, price_type, currency, at),
    )
