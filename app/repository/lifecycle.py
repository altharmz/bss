"""Prepaid lifecycle settings and the log of transitions taken (docs/decisions/prepaid.md)."""

from datetime import date, datetime
from uuid import UUID

import psycopg
from psycopg import sql

from app.billing.lifecycle import Settings
from app.repository._sql import Row, fetch_one, returning

FIELDS = {
    "graceDays": "grace_days",
    "suspensionDays": "suspension_days",
    "numberAgingDays": "number_aging_days",
    "paygZeroBalanceDays": "payg_zero_balance_days",
}


def settings(conn: psycopg.Connection) -> Settings:
    row = get_row(conn)
    return Settings(
        row["grace_days"],
        row["suspension_days"],
        row["number_aging_days"],
        row["payg_zero_balance_days"],
    )


def get_row(conn: psycopg.Connection, *, lock: bool = False) -> Row:
    query = "SELECT * FROM prepaid_lifecycle_setting"
    row = fetch_one(conn, query + (" FOR UPDATE" if lock else ""))
    assert row is not None  # the migration inserts the row
    return row


def update(conn: psycopg.Connection, values: dict[str, int], now: datetime) -> Row:
    columns = {FIELDS[key]: value for key, value in values.items()}  # fixed names, never input
    sets = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
        for column in columns
    )
    query = sql.SQL("UPDATE prepaid_lifecycle_setting SET {}, updated_at = %(now)s RETURNING *")
    return returning(conn, query.format(sets), {**columns, "now": now})


def claim_attempt(
    conn: psycopg.Connection,
    *,
    product_id: UUID,
    account_id: UUID,
    action: str,
    day: date,
    now: datetime,
) -> UUID | None:
    """Record that `action` is tried for the product on `day`; None if it was tried already
    (a refused transition is retried the next business date, not on every tick)."""
    row = fetch_one(
        conn,
        "INSERT INTO prepaid_lifecycle_event (product_id, billing_account_id, action,"
        " business_date, outcome, created_at) VALUES (%s, %s, %s, %s, 'placed', %s)"
        " ON CONFLICT (product_id, action, business_date) DO NOTHING RETURNING id",
        (product_id, account_id, action, day, now),
    )
    return None if row is None else row["id"]


def settle(
    conn: psycopg.Connection,
    event_id: UUID,
    *,
    order_id: UUID | None,
    rejected: str | None,
) -> None:
    conn.execute(
        "UPDATE prepaid_lifecycle_event SET product_order_id = %s, outcome = %s, reason = %s"
        " WHERE id = %s",
        (order_id, "rejected" if rejected else "placed", rejected, event_id),
    )
