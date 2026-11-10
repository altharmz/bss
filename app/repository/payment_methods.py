"""Saved cards (TMF670 tokenizedCard) of billing accounts: gateway tokens, never numbers.

A removed card stays (status `removed`) so charges made with it keep their reference; it
is neither listed nor usable. At most one active card per account is the default.
docs/decisions/payments.md.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM payment_method WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid,))


def active(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    row = get(conn, uid, lock=lock)
    return row if row is not None and row["status"] == "active" else None


def default_of(conn: psycopg.Connection, account_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM payment_method WHERE billing_account_id = %s AND is_default",
        (account_id,),
    )


def page(
    conn: psycopg.Connection, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    clauses = ["status = 'active'"]
    args: dict[str, Any] = {"off": offset, "lim": limit + 1}
    if "billing_account_id" in where:
        clauses.append("billing_account_id = %(account)s")
        args["account"] = where["billing_account_id"]
    if "id" in where:
        clauses.append("id = %(id)s")
        args["id"] = where["id"]
    if "is_default" in where:
        clauses.append("is_default = %(default)s")
        args["default"] = where["is_default"]
    clause = " WHERE " + " AND ".join(clauses)
    rows = fetch_all(
        conn,
        f"SELECT * FROM payment_method{clause} ORDER BY created_at, id"  # noqa: S608
        " LIMIT %(lim)s OFFSET %(off)s",
        args,
    )
    return rows[:limit], len(rows) > limit


def insert(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    name: str,
    description: str | None,
    brand: str | None,
    card_type: str | None,
    last_four: str | None,
    expiration_date: datetime | None,
    token: str,
    preferred: bool | None,
    now: datetime,
) -> Row:
    """The first active card of an account becomes its default; `preferred` makes this
    one the default (and the previous one not)."""
    has_default = default_of(conn, account_id) is not None
    make_default = bool(preferred) or (preferred is None and not has_default)
    if make_default:
        clear_default(conn, account_id)
    return returning(
        conn,
        "INSERT INTO payment_method (billing_account_id, name, description, brand, card_type,"
        " last_four, expiration_date, gateway_token, is_default, created_at, status_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            account_id,
            name,
            description,
            brand,
            card_type,
            last_four,
            expiration_date,
            token,
            make_default,
            now,
            now,
        ),
    )


def clear_default(conn: psycopg.Connection, account_id: UUID) -> None:
    conn.execute(
        "UPDATE payment_method SET is_default = false WHERE billing_account_id = %s AND is_default",
        (account_id,),
    )


def update(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    """`name`, `description`, `is_default`; making one the default unmakes the other."""
    row = get(conn, uid, lock=True)
    if changes.get("is_default"):
        clear_default(conn, row["billing_account_id"])
    names = list(changes)
    sets = ", ".join(f"{name} = %({name})s" for name in names)  # fixed column names
    return returning(
        conn,
        f"UPDATE payment_method SET {sets} WHERE id = %(id)s RETURNING *",  # noqa: S608
        {**changes, "id": uid},
    )


def remove(conn: psycopg.Connection, uid: UUID, *, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE payment_method SET status = 'removed', is_default = false, status_at = %s"
        " WHERE id = %s RETURNING *",
        (now, uid),
    )
