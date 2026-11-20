"""What a signed-in customer may reach in self-care (docs/decisions/self-care.md, the scope
rule). Every function takes the session's `customer_id`; an object of another customer is
None, which the API answers with a 404 (never a 403), so that ids are not confirmed."""

from datetime import datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one


def accounts(conn: psycopg.Connection, customer_id: UUID) -> list[Row]:
    """The customer's billing accounts, oldest first."""
    return fetch_all(
        conn,
        "SELECT * FROM billing_account WHERE customer_id = %s ORDER BY created_at, id",
        (customer_id,),
    )


def account(
    conn: psycopg.Connection, customer_id: UUID, account_id: UUID, *, lock: bool = False
) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM billing_account WHERE id = %s AND customer_id = %s"  # noqa: S608 - fixed SQL
        + (" FOR UPDATE" if lock else ""),
        (account_id, customer_id),
    )


def product(conn: psycopg.Connection, customer_id: UUID, product_id: UUID) -> Row | None:
    """A product of one of the customer's accounts."""
    return fetch_one(
        conn,
        "SELECT p.* FROM product p JOIN billing_account a ON a.id = p.billing_account_id"
        " WHERE p.id = %s AND a.customer_id = %s",
        (product_id, customer_id),
    )


def payment_method(
    conn: psycopg.Connection, customer_id: UUID, method_id: UUID, *, lock: bool = False
) -> Row | None:
    """An active saved card of one of the customer's accounts."""
    return fetch_one(
        conn,
        "SELECT m.* FROM payment_method m JOIN billing_account a ON a.id = m.billing_account_id"  # noqa: S608 - fixed SQL
        " WHERE m.id = %s AND a.customer_id = %s AND m.status = 'active'"
        + (" FOR UPDATE OF m" if lock else ""),
        (method_id, customer_id),
    )


def payment_methods(conn: psycopg.Connection, customer_id: UUID) -> list[Row]:
    """The active saved cards of the customer's accounts, oldest first."""
    return fetch_all(
        conn,
        "SELECT m.* FROM payment_method m JOIN billing_account a ON a.id = m.billing_account_id"
        " WHERE a.customer_id = %s AND m.status = 'active' ORDER BY m.created_at, m.id",
        (customer_id,),
    )


def payment_command(conn: psycopg.Connection, customer_id: UUID, command_id: UUID) -> Row | None:
    """A card top-up of one of the customer's accounts."""
    return fetch_one(
        conn,
        "SELECT c.* FROM payment_command c JOIN billing_account a ON a.id = c.billing_account_id"
        " WHERE c.id = %s AND a.customer_id = %s AND c.purpose = 'topup'",
        (command_id, customer_id),
    )


def offerings(conn: psycopg.Connection, at: datetime) -> list[Row]:
    """The offerings ordering accepts at `at` (sellable, Active, valid), by name."""
    return fetch_all(
        conn,
        "SELECT * FROM product_offering WHERE is_sellable AND lifecycle_status = 'Active'"
        " AND (valid_for IS NULL OR valid_for @> %s::timestamptz) ORDER BY name, id",
        (at,),
    )
