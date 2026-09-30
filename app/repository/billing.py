"""Billing runs, applied customer billing rates and customer bills (TMF678).

Only the billing run writes ABRs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.range import Range

from app.repository._sql import Row, fetch_all, fetch_one, money, returning, select_page


class BillingRunConflict(Exception):
    """A run for this date is already running or has already completed."""

    def __init__(self, run: Row) -> None:
        super().__init__(f"billing run for {run['run_date']} is {run['status']}")
        self.run = run


def start_run(conn: psycopg.Connection, run_date: date, *, now: datetime) -> Row:
    """Start the run for `run_date`, or restart it if the previous attempt failed.

    A retry reuses the same row (run_date is UNIQUE). Raises BillingRunConflict if the
    run is 'running' or 'completed'. A stale 'running' row left by a killed process is not
    taken over yet - see CLAUDE.md, open questions.
    """
    run = fetch_one(
        conn,
        """
        INSERT INTO billing_run (run_date, status, started_at)
        VALUES (%s, 'running', %s)
        ON CONFLICT (run_date) DO UPDATE
            SET status = 'running', started_at = EXCLUDED.started_at,
                finished_at = NULL, error = NULL
            WHERE billing_run.status = 'failed'
        RETURNING *
        """,
        (run_date, now),
    )
    if run is None:
        existing = fetch_one(conn, "SELECT * FROM billing_run WHERE run_date = %s", (run_date,))
        assert existing is not None
        raise BillingRunConflict(existing)
    return run


def _end_run(
    conn: psycopg.Connection, run_id: UUID, status: str, now: datetime, error: str | None
) -> Row:
    run = fetch_one(
        conn,
        """
        UPDATE billing_run SET status = %s, finished_at = %s, error = %s
        WHERE id = %s AND status = 'running' RETURNING *
        """,
        (status, now, error, run_id),
    )
    if run is None:
        raise LookupError(f"no running billing run {run_id}")
    return run


def finish_run(conn: psycopg.Connection, run_id: UUID, *, now: datetime) -> Row:
    return _end_run(conn, run_id, "completed", now, None)


def fail_run(conn: psycopg.Connection, run_id: UUID, *, now: datetime, error: str) -> Row:
    return _end_run(conn, run_id, "failed", now, error)


def get_run(conn: psycopg.Connection, run_date: date) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_run WHERE run_date = %s", (run_date,))


def insert_abr(
    conn: psycopg.Connection,
    *,
    product_id: UUID,
    billing_account_id: UUID,
    charge_type: str,
    amount: Decimal,
    currency: str,
    period: Range,
    billing_run_id: UUID,
    refunds_rate_id: UUID | None = None,
    customer_bill_id: UUID | None = None,
) -> Row:
    """Insert an applied customer billing rate. Overlaps are rejected by the database
    (EXCLUDE constraint) - note that ON CONFLICT with a target cannot catch those."""
    return returning(
        conn,
        """
        INSERT INTO applied_customer_billing_rate
            (product_id, billing_account_id, charge_type, amount, currency, period,
             billing_run_id, refunds_rate_id, customer_bill_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            product_id,
            billing_account_id,
            charge_type,
            money(amount),
            currency,
            period,
            billing_run_id,
            refunds_rate_id,
            customer_bill_id,
        ),
    )


def list_abrs(
    conn: psycopg.Connection, *, product_id: UUID, period: Range | None = None
) -> list[Row]:
    """ABRs of a product, optionally only those whose period overlaps `period`."""
    if period is None:
        return fetch_all(
            conn,
            """
            SELECT * FROM applied_customer_billing_rate
            WHERE product_id = %s ORDER BY lower(period), charge_type
            """,
            (product_id,),
        )
    return fetch_all(
        conn,
        """
        SELECT * FROM applied_customer_billing_rate
        WHERE product_id = %s AND period && %s::daterange
        ORDER BY lower(period), charge_type
        """,
        (product_id, period),
    )


def insert_bill(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID,
    currency: str,
    bill_period: Range,
    bill_date: date,
    amount_due: Decimal,
    billing_run_id: UUID,
    state: str = "new",
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO customer_bill
            (billing_account_id, currency, bill_period, bill_date, state, amount_due,
             billing_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            billing_account_id,
            currency,
            bill_period,
            bill_date,
            state,
            money(amount_due),
            billing_run_id,
        ),
    )


def account_balance(conn: psycopg.Connection, billing_account_id: UUID) -> Decimal:
    """Payments received minus everything charged. Refund ABRs are stored negative, so
    subtracting the sum of all ABRs already credits them. Positive = customer in credit.

    There is deliberately no balance column: it is always computed.
    """
    row = fetch_one(
        conn,
        """
        SELECT
            (SELECT coalesce(sum(amount), 0) FROM payment
             WHERE billing_account_id = %(id)s AND status = 'done')
          - (SELECT coalesce(sum(amount), 0) FROM applied_customer_billing_rate
             WHERE billing_account_id = %(id)s) AS balance
        """,
        {"id": billing_account_id},
    )
    assert row is not None
    return row["balance"]


def get_abr(conn: psycopg.Connection, abr_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM applied_customer_billing_rate WHERE id = %s", (abr_id,))


def page_abrs(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(
        conn, "applied_customer_billing_rate", where=where, offset=offset, limit=limit
    )


def get_bill(conn: psycopg.Connection, bill_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM customer_bill WHERE id = %s", (bill_id,))


def page_bills(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "customer_bill", where=where, offset=offset, limit=limit)
