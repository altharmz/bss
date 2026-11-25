"""Postpaid dunning: the settings, what accounts owe past due, the steps taken
(docs/decisions/dunning.md)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.billing.dunning import Settings
from app.repository._sql import Row, fetch_all, fetch_one, returning

FIELDS = {
    "reminder1Days": "reminder1_days",
    "reminder2Days": "reminder2_days",
    "suspendDays": "suspend_days",
    "minimumOverdue": "minimum_overdue",
}

# What each postpaid account owes on its open (sent, partiallyPaid), positive bills past their
# due date: held bills (onHold) never count. `first_due` is the oldest such due date, so O is
# first_due + 1. The partial index customer_bill_open_due_idx serves the date filter.
_OVERDUE = """
    SELECT b.billing_account_id, a.currency, min(b.payment_due_date) AS first_due,
           sum(greatest(b.amount_due - coalesce(paid.amount, 0), 0)) AS amount
    FROM customer_bill b
    JOIN billing_account a ON a.id = b.billing_account_id
    LEFT JOIN LATERAL (
        SELECT sum(al.amount) AS amount FROM bill_allocation al WHERE al.customer_bill_id = b.id
    ) paid ON true
    WHERE b.state IN ('sent', 'partiallyPaid') AND b.amount_due > 0
      AND b.payment_due_date < %(today)s
      AND a.rating_type = 'postpaid' AND a.state = 'Active' AND NOT a.dunning_exempt
"""
_GROUP = " GROUP BY b.billing_account_id, a.currency"
# The accounts a dunning pass looks at (`candidates`): with a dunning suspension to lift, and
# (the full pass) with a bill past due.
_SUSPENDED_ACCOUNTS = (
    "SELECT DISTINCT p.billing_account_id AS id FROM product p"
    " JOIN billing_account a ON a.id = p.billing_account_id"
    " WHERE p.dunning_suspended AND p.status = 'suspended' AND p.termination_date IS NULL"
    " AND a.rating_type = 'postpaid' AND a.state = 'Active'"
)
_OVERDUE_ACCOUNTS = (
    " UNION SELECT b.billing_account_id FROM customer_bill b"
    " JOIN billing_account a ON a.id = b.billing_account_id"
    " WHERE b.state IN ('sent', 'partiallyPaid') AND b.amount_due > 0"
    " AND b.payment_due_date < %(today)s AND a.rating_type = 'postpaid' AND a.state = 'Active'"
)


# The accounts in dunning (`page`), after the `overdue` CTE.
_PAGE = """
        dunning AS (
            SELECT o.billing_account_id, o.currency, o.first_due + 1 AS overdue_since, o.amount,
                   coalesce((SELECT s.step FROM dunning_step s
                              WHERE s.billing_account_id = o.billing_account_id
                                AND s.overdue_since = o.first_due + 1 AND s.step <> 'restore'
                              ORDER BY array_position(
                                  ARRAY['reminder1', 'reminder2', 'suspend'], s.step) DESC
                              LIMIT 1), 'none') AS last_step,
                   (SELECT s.business_date FROM dunning_step s
                     WHERE s.billing_account_id = o.billing_account_id
                       AND s.overdue_since = o.first_due + 1 AND s.step = 'suspend')
                       AS suspended_since
            FROM overdue o WHERE o.amount > 0 AND o.amount >= %(minimum)s)
        SELECT d.*, a.name FROM dunning d JOIN billing_account a ON a.id = d.billing_account_id
        WHERE (%(step)s::text IS NULL OR d.last_step = %(step)s)
          AND (%(by)s::date IS NULL OR d.suspended_since <= %(by)s)
          AND (%(account)s::uuid IS NULL OR d.billing_account_id = %(account)s)
        ORDER BY d.overdue_since, d.billing_account_id OFFSET %(offset)s LIMIT %(limit)s
    """


def settings(conn: psycopg.Connection) -> Settings:
    row = get_row(conn)
    return Settings(
        row["reminder1_days"],
        row["reminder2_days"],
        row["suspend_days"],
        row["minimum_overdue"],
    )


def get_row(conn: psycopg.Connection, *, lock: bool = False) -> Row:
    query = "SELECT * FROM dunning_setting"
    row = fetch_one(conn, query + (" FOR UPDATE" if lock else ""))
    assert row is not None  # the migration inserts the row
    return row


def update(conn: psycopg.Connection, values: dict[str, Any], now: datetime) -> Row:
    columns = {FIELDS[key]: value for key, value in values.items()}  # fixed names, never input
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
        for column in columns
    )
    query = sql.SQL("UPDATE dunning_setting SET {}, updated_at = %(now)s RETURNING *")
    return returning(conn, query.format(sets), {**columns, "now": now})


def overdue_of(conn: psycopg.Connection, account_id: UUID, today: date) -> Row | None:
    """What the account owes past due on `today`; None if nothing (or it is not an Active,
    non-exempt postpaid account)."""
    return fetch_one(
        conn,
        _OVERDUE + " AND b.billing_account_id = %(account)s" + _GROUP,
        {"today": today, "account": account_id},
    )


def candidates(conn: psycopg.Connection, today: date, *, full: bool) -> list[UUID]:
    """The accounts a dunning pass looks at: with `full`, every Active postpaid account with a
    bill past due or a dunning suspension to lift; otherwise only the latter (a payment
    restores the lines within a tick, while the steps follow the business date)."""
    query = _SUSPENDED_ACCOUNTS + (_OVERDUE_ACCOUNTS if full else "") + " ORDER BY id"
    return [row["id"] for row in fetch_all(conn, query, {"today": today})]


def steps_of(conn: psycopg.Connection, account_id: UUID, o: date) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM dunning_step WHERE billing_account_id = %s AND overdue_since = %s"
        " ORDER BY business_date, taken_at",
        (account_id, o),
    )


def latest_suspension(conn: psycopg.Connection, account_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM dunning_step WHERE billing_account_id = %s AND step = 'suspend'"
        " ORDER BY overdue_since DESC LIMIT 1",
        (account_id,),
    )


def record_step(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    o: date,
    step: str,
    day: date,
    amount: Decimal,
    now: datetime,
) -> bool:
    """Record a step for the account and O; False if it was taken already."""
    row = fetch_one(
        conn,
        "INSERT INTO dunning_step (billing_account_id, overdue_since, step, business_date,"
        " overdue_amount, taken_at) VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING step",
        (account_id, o, step, day, amount, now),
    )
    return row is not None


def set_products(
    conn: psycopg.Connection, account_id: UUID, o: date, step: str, products: int
) -> None:
    conn.execute(
        "UPDATE dunning_step SET products = %s WHERE billing_account_id = %s"
        " AND overdue_since = %s AND step = %s",
        (products, account_id, o, step),
    )


def suspended_products(conn: psycopg.Connection, account_id: UUID) -> list[Row]:
    """The account's products under a dunning suspension, locked, in id order."""
    return fetch_all(
        conn,
        "SELECT * FROM product WHERE billing_account_id = %s AND dunning_suspended"
        " AND status = 'suspended' AND termination_date IS NULL ORDER BY id FOR UPDATE",
        (account_id,),
    )


def suspended_ids(conn: psycopg.Connection, account_id: UUID) -> list[UUID]:
    """The account's products under a dunning suspension (not locked), in id order."""
    rows = fetch_all(
        conn,
        "SELECT id FROM product WHERE billing_account_id = %s AND dunning_suspended"
        " AND status = 'suspended' AND termination_date IS NULL ORDER BY id",
        (account_id,),
    )
    return [row["id"] for row in rows]


def active_products(conn: psycopg.Connection, account_id: UUID, today: date) -> list[Row]:
    """The account's products a suspension applies to: active, started, no end set; locked."""
    return fetch_all(
        conn,
        "SELECT * FROM product WHERE billing_account_id = %s AND status = 'active'"
        " AND start_date <= %s AND termination_date IS NULL ORDER BY id FOR UPDATE",
        (account_id, today),
    )


def page(
    conn: psycopg.Connection,
    today: date,
    minimum: Decimal,
    *,
    step: str | None,
    suspended_by: date | None,
    account_id: UUID | None = None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    """The accounts in dunning on `today`, oldest O first: O, the amount past due, the last
    step taken for that O (`none` before the first) and the suspension's date. `step` keeps
    those whose last step it is; `suspended_by` those suspended on or before that date;
    `account_id` that one account."""
    query = "WITH overdue AS (" + _OVERDUE + _GROUP + ")," + _PAGE  # noqa: S608 - fixed SQL
    rows = fetch_all(
        conn,
        query,
        {
            "today": today,
            "minimum": minimum,
            "step": step,
            "by": suspended_by,
            "account": account_id,
            "offset": offset,
            "limit": limit + 1,
        },
    )
    return rows[:limit], len(rows) > limit
