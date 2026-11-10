"""Account PINs, line locks and the account audit log (docs/decisions/accounts.md)."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning

ACTIONS = (
    "sim_swap",
    "number_change",
    "number_transfer_pin",
    "contact_change",
    "port_out",
    "pin_set",
    "pin_reset",
    "lock_set",
    "lock_cleared",
    "pin_lockout",
)
OUTCOMES = ("done", "refused")

# --- PIN ---


def pin_of(conn: psycopg.Connection, customer_id: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM customer_pin WHERE customer_id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (customer_id,))


def pins_of(conn: psycopg.Connection, customer_ids: list[UUID]) -> list[Row]:
    """The PIN rows of the customers, locked (a verification updates its counters)."""
    return fetch_all(
        conn,
        "SELECT * FROM customer_pin WHERE customer_id = ANY(%s) ORDER BY customer_id FOR UPDATE",
        (customer_ids,),
    )


def store_pin(
    conn: psycopg.Connection, customer_id: UUID, pin_hash: str, operator: str, reason: str, now
) -> Row:
    return returning(
        conn,
        "INSERT INTO customer_pin (customer_id, pin_hash, set_at, set_by, reason)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (customer_id) DO UPDATE SET"
        " pin_hash = EXCLUDED.pin_hash, set_at = EXCLUDED.set_at, set_by = EXCLUDED.set_by,"
        " reason = EXCLUDED.reason, failed_attempts = 0, locked_until = NULL RETURNING *",
        (customer_id, pin_hash, now, operator, reason),
    )


def pin_ok(conn: psycopg.Connection, customer_id: UUID) -> None:
    conn.execute(
        "UPDATE customer_pin SET failed_attempts = 0, locked_until = NULL"
        " WHERE customer_id = %s AND (failed_attempts <> 0 OR locked_until IS NOT NULL)",
        (customer_id,),
    )


def pin_failed(
    conn: psycopg.Connection, customer_id: UUID, *, maximum: int, until: datetime | None
) -> Row:
    """Count a wrong PIN; at `maximum` the verification locks until `until` and the count
    restarts."""
    return returning(
        conn,
        "UPDATE customer_pin SET failed_attempts = CASE WHEN failed_attempts + 1 >= %s THEN 0"
        " ELSE failed_attempts + 1 END, locked_until = CASE WHEN failed_attempts + 1 >= %s"
        " THEN %s ELSE locked_until END WHERE customer_id = %s RETURNING *",
        (maximum, maximum, until, customer_id),
    )


# --- who is protected ---


def customer_of_product(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    """{customer_id, billing_account_id, rating_type} of a product."""
    return fetch_one(
        conn,
        "SELECT a.customer_id, a.id AS billing_account_id, a.rating_type FROM product p"
        " JOIN billing_account a ON a.id = p.billing_account_id WHERE p.id = %s",
        (product_id,),
    )


def protected(conn: psycopg.Connection, customer_ids: list[UUID]) -> list[UUID]:
    """Of the customers, those with a live product on a prepaid account (a line to take over)."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT a.customer_id FROM billing_account a JOIN product p"
        " ON p.billing_account_id = a.id WHERE a.customer_id = ANY(%s)"
        " AND a.rating_type = 'prepaid' AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
        " AND p.termination_date IS NULL ORDER BY a.customer_id",
        (customer_ids,),
    )
    return [row["customer_id"] for row in rows]


def customers_of_party(conn: psycopg.Connection, party_id: UUID) -> list[UUID]:
    return [
        row["id"]
        for row in fetch_all(
            conn, "SELECT id FROM customer WHERE party_id = %s ORDER BY id", (party_id,)
        )
    ]


def customer_of_account(conn: psycopg.Connection, account_id: UUID) -> UUID | None:
    row = fetch_one(conn, "SELECT customer_id FROM billing_account WHERE id = %s", (account_id,))
    return None if row is None else row["customer_id"]


def first_account(conn: psycopg.Connection, customer_id: UUID) -> UUID | None:
    row = fetch_one(
        conn,
        "SELECT id FROM billing_account WHERE customer_id = %s ORDER BY created_at, id LIMIT 1",
        (customer_id,),
    )
    return None if row is None else row["id"]


# --- locks ---


def lock_of(conn: psycopg.Connection, product_id: UUID, *, update: bool = False) -> Row | None:
    query = "SELECT * FROM line_lock WHERE product_id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if update else ""), (product_id,))


def store_lock(
    conn: psycopg.Connection,
    product_id: UUID,
    *,
    sim_swap: bool,
    port_out: bool,
    operator: str,
    reason: str,
    now,
) -> Row:
    return returning(
        conn,
        "INSERT INTO line_lock (product_id, sim_swap, port_out, updated_at, updated_by, reason)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (product_id) DO UPDATE SET"
        " sim_swap = EXCLUDED.sim_swap, port_out = EXCLUDED.port_out,"
        " updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by,"
        " reason = EXCLUDED.reason RETURNING *",
        (product_id, sim_swap, port_out, now, operator, reason),
    )


# --- audit ---


def record(
    conn: psycopg.Connection,
    *,
    now: datetime,
    operator: str,
    role: str | None,
    action: str,
    outcome: str,
    verification: str,
    reason: str | None = None,
    customer_id: UUID | None = None,
    billing_account_id: UUID | None = None,
    product_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> Row:
    return returning(
        conn,
        "INSERT INTO account_audit (id, at, operator, role, customer_id, billing_account_id,"
        " product_id, action, outcome, reason, verification, detail)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            uuid4(),
            now,
            operator,
            role,
            customer_id,
            billing_account_id,
            product_id,
            action,
            outcome,
            reason,
            verification,
            jsonb(detail or {}),
        ),
    )


_FILTERS = {
    "customer_id": "customer_id",
    "billing_account_id": "billing_account_id",
    "product_id": "product_id",
    "action": "action",
    "outcome": "outcome",
    "operator": "operator",
}


def audit_page(
    conn: psycopg.Connection, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    conditions = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(_FILTERS[key])) for key in where
    )
    clause = sql.SQL(" WHERE ") + conditions if where else sql.SQL("")
    params = list(where.values())
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM account_audit")
        + clause
        + sql.SQL(" ORDER BY at DESC, seq DESC OFFSET %s LIMIT %s"),
        [*params, offset, limit + 1],
    )
    return rows[:limit], len(rows) > limit


def audit_since(conn: psycopg.Connection, since: datetime) -> list[Row]:
    """The entries the customer is told about: done actions, and a port-out that a lock stopped."""
    return fetch_all(
        conn,
        "SELECT * FROM account_audit WHERE at >= %s AND (outcome = 'done'"
        " OR (action = 'port_out' AND outcome = 'refused')) ORDER BY at, seq",
        (since,),
    )
