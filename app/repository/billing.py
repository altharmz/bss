"""Billing runs, applied customer billing rates and customer bills (TMF678).

Only the billing run writes ABRs. The `load_*` / `write_*` functions at the end map rows
to and from the billing core's dataclasses (app/billing/model.py); the core never sees a
row.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.range import Range

from app.billing.model import (
    Account,
    DateRange,
    NewAbr,
    NewBill,
    Price,
    Product,
    StoredAbr,
    StoredBill,
)
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


# --- the billing core's view of one account (rows <-> app/billing/model.py) ---

# A product whose termination order is not visible yet is passed with the status it had
# before that order: a cancellation only ever happens to a product that has not started.
STATUS_BEFORE_TERMINATION = {
    "cancelled": "created",
    "terminated": "active",
    "pendingTerminate": "active",
}


def _date_range(value: Range) -> DateRange:
    return DateRange(value.lower, value.upper)


def _daterange(value: DateRange) -> Range:
    return Range(value.start, value.end, "[)")


def running_run_date(conn: psycopg.Connection, run_id: UUID) -> date:
    """The run date of a run that is still `running`, locked against a concurrent finish."""
    row = fetch_one(
        conn, "SELECT run_date, status FROM billing_run WHERE id = %s FOR SHARE", (run_id,)
    )
    if row is None or row["status"] != "running":
        raise LookupError(f"billing run {run_id} is not running")
    return row["run_date"]


def load_account(conn: psycopg.Connection, account_id: UUID) -> Account:
    """The account, locked until the transaction ends: two runs billing the same account
    concurrently would both see it unbilled."""
    row = fetch_one(
        conn,
        "SELECT id, currency, bill_cycle_day FROM billing_account WHERE id = %s FOR UPDATE",
        (account_id,),
    )
    if row is None:
        raise LookupError(f"billing account {account_id} does not exist")
    return Account(id=row["id"], currency=row["currency"], bill_cycle_day=row["bill_cycle_day"])


def load_products(
    conn: psycopg.Connection, account_id: UUID, *, visible_before: datetime
) -> tuple[Product, ...]:
    """The account's products as known at `visible_before`: a product exists once the
    order that created it completed before that instant, and its termination (or
    cancellation) counts once the order that set it did. Later orders are invisible."""
    rows = fetch_all(
        conn,
        """
        SELECT p.id, p.product_offering_id, p.status, p.start_date, p.termination_date,
               p.terminated_by_item_id IS NULL
                   OR coalesce(t.completion_date < %(before)s, false) AS termination_known
        FROM product p
        JOIN product_order_item ci ON ci.id = p.created_by_item_id
        JOIN product_order c ON c.id = ci.product_order_id
        LEFT JOIN product_order_item ti ON ti.id = p.terminated_by_item_id
        LEFT JOIN product_order t ON t.id = ti.product_order_id
        WHERE p.billing_account_id = %(account)s AND c.completion_date < %(before)s
        ORDER BY p.id
        """,
        {"account": account_id, "before": visible_before},
    )
    return tuple(
        Product(
            id=row["id"],
            offering_id=row["product_offering_id"],
            status=row["status"]
            if row["termination_known"]
            else STATUS_BEFORE_TERMINATION.get(row["status"], row["status"]),
            start_date=row["start_date"],
            termination_date=row["termination_date"] if row["termination_known"] else None,
        )
        for row in rows
    )


def load_recurring_prices(conn: psycopg.Connection, offering_ids: list[UUID]) -> tuple[Price, ...]:
    """Every monthly recurring price attached to these offerings, in any currency. Not
    filtered by time: a price change only takes effect at or after the moment it was made,
    and the core looks prices up at midnights <= as_of."""
    rows = fetch_all(
        conn,
        """
        SELECT id, product_offering_id, amount, currency, valid_for
        FROM product_offering_price
        WHERE product_offering_id = ANY(%s)
          AND price_type = 'recurring' AND recurring_period = 'monthly'
          AND recurring_period_length = 1
        ORDER BY id
        """,
        (sorted(set(offering_ids)),),
    )
    return tuple(
        Price(
            id=row["id"],
            offering_id=row["product_offering_id"],
            amount=row["amount"],
            currency=row["currency"],
            valid_from=row["valid_for"].lower,
            valid_to=row["valid_for"].upper,
        )
        for row in rows
    )


def load_abrs(conn: psycopg.Connection, account_id: UUID) -> tuple[StoredAbr, ...]:
    rows = fetch_all(
        conn,
        """
        SELECT a.*, r.run_date
        FROM applied_customer_billing_rate a JOIN billing_run r ON r.id = a.billing_run_id
        WHERE a.billing_account_id = %s
        ORDER BY lower(a.period), a.charge_type, a.id
        """,
        (account_id,),
    )
    return tuple(
        StoredAbr(
            id=row["id"],
            product_id=row["product_id"],
            kind=row["charge_type"],
            amount=row["amount"],
            currency=row["currency"],
            period=_date_range(row["period"]),
            refunds_id=row["refunds_rate_id"],
            bill_id=row["customer_bill_id"],
            run_date=row["run_date"],
        )
        for row in rows
    )


def load_bills(conn: psycopg.Connection, account_id: UUID) -> tuple[StoredBill, ...]:
    rows = fetch_all(
        conn,
        "SELECT id, bill_period, bill_date FROM customer_bill WHERE billing_account_id = %s"
        " ORDER BY bill_date",
        (account_id,),
    )
    return tuple(
        StoredBill(id=row["id"], period=_date_range(row["bill_period"]), bill_date=row["bill_date"])
        for row in rows
    )


def write_abr(conn: psycopg.Connection, account: Account, run_id: UUID, abr: NewAbr) -> UUID:
    return insert_abr(
        conn,
        product_id=abr.product_id,
        billing_account_id=account.id,
        charge_type=abr.kind,
        amount=abr.amount,
        currency=abr.currency,
        period=_daterange(abr.period),
        billing_run_id=run_id,
        refunds_rate_id=abr.refunds_id,
    )["id"]


def write_bill(conn: psycopg.Connection, account: Account, run_id: UUID, bill: NewBill) -> UUID:
    return insert_bill(
        conn,
        billing_account_id=account.id,
        currency=bill.currency,
        bill_period=_daterange(bill.period),
        bill_date=bill.bill_date,
        amount_due=bill.amount_due,
        billing_run_id=run_id,
    )["id"]


def attach_abrs_to_bill(
    conn: psycopg.Connection, bill_id: UUID, account_id: UUID, abr_ids: list[UUID]
) -> None:
    """Put these unbilled ABRs of the account on the bill. All of them, or it raises."""
    rows = fetch_all(
        conn,
        """
        UPDATE applied_customer_billing_rate SET customer_bill_id = %s
        WHERE id = ANY(%s) AND billing_account_id = %s AND customer_bill_id IS NULL
        RETURNING id
        """,
        (bill_id, abr_ids, account_id),
    )
    if len(rows) != len(set(abr_ids)):
        raise LookupError(f"bill {bill_id}: {len(abr_ids) - len(rows)} ABRs not attachable")
