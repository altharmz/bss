"""Product catalog (TMF620): specifications, offerings, offering prices."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.range import Range

from app.repository._sql import (
    Row,
    fetch_all,
    fetch_one,
    money,
    returning,
    select_page,
    update_by_id,
)
from app.repository.parties import with_details


def _with_details(
    conn: psycopg.Connection, table: str, row: Row, details: dict[str, Any] | None
) -> Row:
    """`details` (the TMF620 attributes that are documents to us, app/api/catalog.py):
    written only when given, so the tests' factories also work on a schema before 0024."""
    if not details:
        return row
    return update_by_id(conn, table, row["id"], with_details({"details": details}))


def create_product_specification(
    conn: psycopg.Connection,
    *,
    name: str,
    lifecycle_status: str,
    description: str | None = None,
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    """`id` None: a new one (an import passes the source's)."""
    row = returning(
        conn,
        """
        INSERT INTO product_specification (id, name, description, lifecycle_status)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s) RETURNING *
        """,
        (id, name, description, lifecycle_status),
    )
    return _with_details(conn, "product_specification", row, details)


def get_product_specification(conn: psycopg.Connection, spec_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_specification WHERE id = %s", (spec_id,))


def create_product_offering(
    conn: psycopg.Connection,
    *,
    product_specification_id: UUID | None,
    name: str,
    lifecycle_status: str,
    valid_for: Range | None,
    is_sellable: bool = True,
    description: str | None = None,
    code: str | None = None,
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    """`code` is unique: a duplicate raises psycopg.errors.UniqueViolation. `id` None: a
    new one (an import passes the source's). `valid_for` None: valid at any time."""
    row = returning(
        conn,
        """
        INSERT INTO product_offering
            (id, product_specification_id, name, description, lifecycle_status,
             is_sellable, valid_for, code)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            id,
            product_specification_id,
            name,
            description,
            lifecycle_status,
            is_sellable,
            valid_for,
            code,
        ),
    )
    return _with_details(conn, "product_offering", row, details)


def get_product_offering(conn: psycopg.Connection, offering_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product_offering WHERE id = %s", (offering_id,))


def create_product_offering_price(
    conn: psycopg.Connection,
    *,
    product_offering_id: UUID | None,
    price_type: str,
    amount: Decimal | None,
    currency: str | None,
    valid_for: Range,
    recurring_period: str | None = None,
    recurring_period_length: int = 1,
    name: str | None = None,
    lifecycle_status: str = "Active",
    details: dict[str, Any] | None = None,
    id: UUID | None = None,
) -> Row:
    """`product_offering_id=None` creates an unattached price (see `attach_price`); only
    an unattached one may have no amount. `id` None: a new one (an import passes the
    source's)."""
    row = returning(
        conn,
        """
        INSERT INTO product_offering_price
            (id, product_offering_id, name, lifecycle_status, price_type, recurring_period,
             amount, currency, valid_for, recurring_period_length)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            id,
            product_offering_id,
            name,
            lifecycle_status,
            price_type,
            recurring_period,
            None if amount is None else money(amount),
            currency,
            valid_for,
            recurring_period_length,
        ),
    )
    return _with_details(conn, "product_offering_price", row, details)


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


def lock_product_specification(
    conn: psycopg.Connection, spec_id: UUID, *, share: bool = False
) -> Row | None:
    """The specification, locked until the transaction ends: FOR SHARE by a new offering
    that references it, FOR UPDATE by a delete - which then waits for that offering to
    commit and sees it, or goes first and the offering finds no specification."""
    if share:
        return fetch_one(
            conn, "SELECT * FROM product_specification WHERE id = %s FOR SHARE", (spec_id,)
        )
    return fetch_one(
        conn, "SELECT * FROM product_specification WHERE id = %s FOR UPDATE", (spec_id,)
    )


def lock_offering(
    conn: psycopg.Connection, offering_id: UUID, *, share: bool = False
) -> Row | None:
    """The offering, locked until the transaction ends. FOR UPDATE: price changes, PATCH
    and deletes queue behind each other. FOR SHARE: orders run side by side, but wait for
    (and are waited for by) those - an order never validates prices that a concurrent
    delete then removes, and a delete never misses an order still in flight."""
    if share:
        return fetch_one(
            conn, "SELECT * FROM product_offering WHERE id = %s FOR SHARE", (offering_id,)
        )
    return fetch_one(
        conn, "SELECT * FROM product_offering WHERE id = %s FOR UPDATE", (offering_id,)
    )


def delete_unattached_price(conn: psycopg.Connection, price_id: UUID) -> bool:
    """Delete a price that belongs to no offering; False if it was attached meanwhile."""
    row = fetch_one(
        conn,
        "DELETE FROM product_offering_price WHERE id = %s AND product_offering_id IS NULL"
        " RETURNING id",
        (price_id,),
    )
    return row is not None


def recurring_prices_from(
    conn: psycopg.Connection, *, offering_id: UUID, currency: str, at: datetime
) -> list[Row]:
    """The offering's recurring prices in `currency` that are still valid at `at` or start
    later - locked - oldest first."""
    return fetch_all(
        conn,
        """
        SELECT * FROM product_offering_price
        WHERE product_offering_id = %s AND price_type = 'recurring' AND currency = %s
          AND (upper(valid_for) IS NULL OR upper(valid_for) > %s)
        ORDER BY lower(valid_for)
        FOR UPDATE
        """,
        (offering_id, currency, at),
    )


def successor_price(
    conn: psycopg.Connection,
    *,
    offering_id: UUID,
    price_type: str,
    currency: str,
    starts_at: datetime,
    exclude_id: UUID,
) -> Row | None:
    """A price of the offering, same type and currency, starting exactly at `starts_at`."""
    return fetch_one(
        conn,
        """
        SELECT * FROM product_offering_price
        WHERE product_offering_id = %s AND price_type = %s AND currency = %s
          AND lower(valid_for) = %s AND id <> %s
        """,
        (offering_id, price_type, currency, starts_at, exclude_id),
    )


def products_billable_from(
    conn: psycopg.Connection, *, offering_id: UUID, currency: str, day: Any
) -> list[Row]:
    """Products of the offering, on accounts in `currency`, that are still billable on or
    after `day`: not cancelled/aborted and not terminated by then (termination_date is
    exclusive, so termination_date <= day means nothing from `day` on is billed)."""
    return fetch_all(
        conn,
        """
        SELECT p.id FROM product p
        JOIN billing_account a ON a.id = p.billing_account_id
        WHERE p.product_offering_id = %s AND a.currency = %s
          AND p.status NOT IN ('cancelled', 'aborted')
          AND (p.termination_date IS NULL OR p.termination_date > %s)
        ORDER BY p.id
        """,
        (offering_id, currency, day),
    )


def prices_of_offering(conn: psycopg.Connection, offering_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        """
        SELECT * FROM product_offering_price WHERE product_offering_id = %s
        ORDER BY created_at, id
        """,
        (offering_id,),
    )


def attach_price(conn: psycopg.Connection, price_id: UUID, offering_id: UUID) -> Row | None:
    """Attach an unattached price to an offering. Returns None if the price does not
    exist or already belongs to an offering - a price is never moved or detached, because
    billing needs the historical price. Raises ExclusionViolation if it would overlap a
    price of the same type and currency already on the offering."""
    return fetch_one(
        conn,
        """
        UPDATE product_offering_price SET product_offering_id = %s
        WHERE id = %s AND product_offering_id IS NULL RETURNING *
        """,
        (offering_id, price_id),
    )


def list_product_specifications(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "product_specification", where=where, offset=offset, limit=limit)


def list_product_offerings(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "product_offering", where=where, offset=offset, limit=limit)


def list_product_offering_prices(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "product_offering_price", where=where, offset=offset, limit=limit)


def update_product_specification(
    conn: psycopg.Connection, spec_id: UUID, changes: dict[str, Any]
) -> Row:
    return update_by_id(conn, "product_specification", spec_id, with_details(changes))


def update_product_offering(
    conn: psycopg.Connection, offering_id: UUID, changes: dict[str, Any]
) -> Row:
    return update_by_id(conn, "product_offering", offering_id, with_details(changes))


def update_product_offering_price(
    conn: psycopg.Connection, price_id: UUID, changes: dict[str, Any]
) -> Row:
    return update_by_id(conn, "product_offering_price", price_id, with_details(changes))
