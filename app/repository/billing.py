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
from app.config import settings
from app.repository._sql import Row, fetch_all, fetch_one, money, returning, select_page


class RunNotComplete(Exception):
    """A run cannot be completed: some member account has no ok outcome."""


# --- billing runs: the rows only. Whether a run is started, reopened, taken over or left
# alone is decided by the worker (app/worker/runs.py), which holds the ownership lock. ---


def insert_run(conn: psycopg.Connection, run_date: date, *, now: datetime, trigger: str) -> Row:
    """A new running run for `run_date` (UNIQUE: UniqueViolation if one exists)."""
    return returning(
        conn,
        """
        INSERT INTO billing_run (run_date, status, started_at, trigger)
        VALUES (%s, 'running', %s, %s) RETURNING *
        """,
        (run_date, now, trigger),
    )


def lock_run(conn: psycopg.Connection, run_date: date) -> Row | None:
    """The run of `run_date`, locked until the transaction ends, or None."""
    return fetch_one(conn, "SELECT * FROM billing_run WHERE run_date = %s FOR UPDATE", (run_date,))


def reopen_run(conn: psycopg.Connection, run_id: UUID, *, now: datetime, trigger: str) -> Row:
    """A failed run running again: attempt + 1, the failure cleared."""
    run = fetch_one(
        conn,
        """
        UPDATE billing_run
           SET status = 'running', attempt = attempt + 1, trigger = %s, started_at = %s,
               finished_at = NULL, error = NULL, failure_kind = NULL, updated_at = now()
         WHERE id = %s AND status = 'failed'
        RETURNING *
        """,
        (trigger, now, run_id),
    )
    if run is None:
        raise LookupError(f"no failed billing run {run_id}")
    return run


def take_over_run(conn: psycopg.Connection, run_id: UUID, *, now: datetime) -> Row:
    """A running run whose owner is gone, continued by a new one: attempt + 1, its trigger
    kept, started_at = now - the start of this attempt, as on a reopen, so /billing-status
    counts the 2 h grace from the takeover (doc-findings #7)."""
    run = fetch_one(
        conn,
        """
        UPDATE billing_run SET attempt = attempt + 1, started_at = %s, heartbeat_at = %s,
               updated_at = now()
         WHERE id = %s AND status = 'running'
        RETURNING *
        """,
        (now, now, run_id),
    )
    if run is None:
        raise LookupError(f"no running billing run {run_id}")
    return run


def _end_run(
    conn: psycopg.Connection,
    run_id: UUID,
    status: str,
    now: datetime,
    error: str | None,
    failure_kind: str | None,
) -> Row:
    run = fetch_one(
        conn,
        """
        UPDATE billing_run SET status = %s, finished_at = %s, error = %s, failure_kind = %s
        WHERE id = %s AND status = 'running' RETURNING *
        """,
        (status, now, error, failure_kind, run_id),
    )
    if run is None:
        raise LookupError(f"no running billing run {run_id}")
    return run


def finish_run(
    conn: psycopg.Connection, run_id: UUID, *, now: datetime, tz: str | None = None
) -> Row:
    """Mark a running run completed - only when every member account has an ok outcome.
    There are no counters (migration 0004): this check is the "completed means every
    member is ok" rule, and RunNotComplete says what is missing."""
    run = fetch_one(conn, "SELECT * FROM billing_run WHERE id = %s FOR UPDATE", (run_id,))
    if run is None or run["status"] != "running":
        raise LookupError(f"no running billing run {run_id}")
    gaps = outcome_gaps(conn, run_id, run["run_date"], tz)
    if gaps:
        detail = ", ".join(f"{n} {status}" for status, n in sorted(gaps.items()))
        raise RunNotComplete(f"billing run {run['run_date']} has accounts not ok: {detail}")
    return _end_run(conn, run_id, "completed", now, None, None)


def fail_run(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    now: datetime,
    error: str,
    failure_kind: str = "run",
) -> Row:
    """'run': the run itself failed, the worker does not move past its date; 'accounts':
    some account failed or is blocked, later dates go ahead (migration 0004)."""
    return _end_run(conn, run_id, "failed", now, error, failure_kind)


def get_run(conn: psycopg.Connection, run_date: date) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_run WHERE run_date = %s", (run_date,))


def get_run_by_id(conn: psycopg.Connection, run_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_run WHERE id = %s", (run_id,))


def held_back_by(conn: psycopg.Connection) -> Row | None:
    """The earliest run that failed as a whole (failure_kind 'run'), or None. No later
    date is billed until it succeeds."""
    return fetch_one(
        conn,
        "SELECT * FROM billing_run WHERE status = 'failed' AND failure_kind = 'run'"
        " ORDER BY run_date LIMIT 1",
    )


def unfinished_run(conn: psycopg.Connection) -> Row | None:
    """The earliest run still running - left by a worker that was killed or stopped; its
    new owner takes it over."""
    return fetch_one(
        conn, "SELECT * FROM billing_run WHERE status = 'running' ORDER BY run_date LIMIT 1"
    )


def failed_account_runs(conn: psycopg.Connection) -> list[Row]:
    """Runs that failed because of accounts (failure_kind 'accounts'), oldest first."""
    return fetch_all(
        conn,
        "SELECT * FROM billing_run WHERE status = 'failed' AND failure_kind = 'accounts'"
        " ORDER BY run_date",
    )


def latest_run_date(conn: psycopg.Connection) -> date | None:
    row = fetch_one(conn, "SELECT max(run_date) AS latest FROM billing_run")
    return row["latest"] if row else None


_RUN_VIEW = """
    SELECT r.*, m.total, o.ok, o.failed, o.blocked, r.run_date = l.latest AS is_latest
      FROM billing_run r
     CROSS JOIN (SELECT max(run_date) AS latest FROM billing_run) l
     CROSS JOIN LATERAL (
            SELECT count(*) AS total FROM billing_account a
             WHERE a.opened_at < (r.run_date::timestamp AT TIME ZONE %(tz)s)) m
     CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE status = 'ok') AS ok,
                   count(*) FILTER (WHERE status = 'failed') AS failed,
                   count(*) FILTER (WHERE status = 'blocked') AS blocked
              FROM billing_run_account WHERE run_id = r.id) o
"""


def page_runs(
    conn: psycopg.Connection, *, offset: int, limit: int, tz: str | None = None
) -> tuple[list[Row], int]:
    """Runs newest first, each with its member count (accounts opened before 00:00 of its
    date), its outcome counts and whether it is the latest - derived, not stored."""
    rows = fetch_all(
        conn,
        _RUN_VIEW + " ORDER BY r.run_date DESC OFFSET %(offset)s LIMIT %(limit)s",
        {"tz": tz or settings.billing_tz, "offset": offset, "limit": limit},
    )
    total = fetch_one(conn, "SELECT count(*) AS n FROM billing_run")
    assert total is not None
    return rows, total["n"]


def run_view(conn: psycopg.Connection, run_id: UUID, tz: str | None = None) -> Row | None:
    return fetch_one(
        conn, _RUN_VIEW + " WHERE r.id = %(id)s", {"tz": tz or settings.billing_tz, "id": run_id}
    )


def run_charges(conn: psycopg.Connection, run_ids: list[UUID]) -> dict[UUID, list[Row]]:
    """The ABRs each run wrote: count and sum per currency (refunds are negative)."""
    rows = fetch_all(
        conn,
        """
        SELECT billing_run_id, currency, count(*) AS n, sum(amount) AS amount
          FROM applied_customer_billing_rate WHERE billing_run_id = ANY(%s)
         GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (run_ids,),
    )
    result: dict[UUID, list[Row]] = {run_id: [] for run_id in run_ids}
    for row in rows:
        result[row["billing_run_id"]].append(row)
    return result


def page_run_outcomes(
    conn: psycopg.Connection, run_id: UUID, *, status: str | None, offset: int, limit: int
) -> tuple[list[Row], int]:
    """A run's per-account outcomes (optionally of one status), with the account's name
    and the date a blocked row waits for; in account id order."""
    params = {"run": run_id, "status": status, "offset": offset, "limit": limit}
    rows = fetch_all(
        conn,
        """
        SELECT o.*, a.name AS account_name, w.run_date AS waits_for_date
          FROM billing_run_account o
          JOIN billing_account a ON a.id = o.billing_account_id
          LEFT JOIN billing_run w ON w.id = o.waits_for_run_id
         WHERE o.run_id = %(run)s
           AND (%(status)s::text IS NULL OR o.status::text = %(status)s)
         ORDER BY o.billing_account_id OFFSET %(offset)s LIMIT %(limit)s
        """,
        params,
    )
    total = fetch_one(
        conn,
        """
        SELECT count(*) AS n FROM billing_run_account o
         WHERE o.run_id = %(run)s
           AND (%(status)s::text IS NULL OR o.status::text = %(status)s)
        """,
        params,
    )
    assert total is not None
    return rows, total["n"]


def touch_run(conn: psycopg.Connection, run_id: UUID, now: datetime) -> None:
    conn.execute("UPDATE billing_run SET heartbeat_at = %s WHERE id = %s", (now, run_id))


# --- per-(run, account) outcomes (billing_run_account, migration 0004) ---


def run_members(conn: psycopg.Connection, run_date: date, tz: str | None = None) -> list[UUID]:
    """The accounts a run covers: opened before 00:00 of its date in BILLING_TZ (opened_at
    is the injected clock's, migration 0004), any state, in id order."""
    rows = fetch_all(
        conn,
        """
        SELECT id FROM billing_account
         WHERE opened_at < (%(day)s::date::timestamp AT TIME ZONE %(tz)s)
         ORDER BY id
        """,
        {"day": run_date, "tz": tz or settings.billing_tz},
    )
    return [row["id"] for row in rows]


def outcome_gaps(
    conn: psycopg.Connection, run_id: UUID, run_date: date, tz: str | None = None
) -> dict[str, int]:
    """Member accounts of the run without an ok outcome, counted by 'failed', 'blocked' and
    'missing' (no row). Empty: every member is ok."""
    rows = fetch_all(
        conn,
        """
        SELECT coalesce(o.status::text, 'missing') AS status, count(*) AS n
          FROM billing_account a
          LEFT JOIN billing_run_account o
                 ON o.run_id = %(run)s AND o.billing_account_id = a.id
         WHERE a.opened_at < (%(day)s::date::timestamp AT TIME ZONE %(tz)s)
           AND (o.status IS NULL OR o.status <> 'ok')
         GROUP BY 1
        """,
        {"run": run_id, "day": run_date, "tz": tz or settings.billing_tz},
    )
    return {row["status"]: row["n"] for row in rows}


def outcomes(conn: psycopg.Connection, run_id: UUID) -> dict[UUID, Row]:
    rows = fetch_all(conn, "SELECT * FROM billing_run_account WHERE run_id = %s", (run_id,))
    return {row["billing_account_id"]: row for row in rows}


def record_outcome(
    conn: psycopg.Connection,
    run_id: UUID,
    account_id: UUID,
    status: str,
    *,
    now: datetime,
    error: str | None = None,
    waits_for: UUID | None = None,
) -> Row:
    """The account's outcome in this run; a second outcome replaces the first and counts an
    attempt."""
    return returning(
        conn,
        """
        INSERT INTO billing_run_account
            (run_id, billing_account_id, status, error, waits_for_run_id, finished_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, billing_account_id) DO UPDATE
            SET status = EXCLUDED.status, error = EXCLUDED.error,
                waits_for_run_id = EXCLUDED.waits_for_run_id,
                attempts = billing_run_account.attempts + 1,
                finished_at = EXCLUDED.finished_at, updated_at = now()
        RETURNING *
        """,
        (run_id, account_id, status, error, waits_for, now),
    )


def earliest_failure_before(
    conn: psycopg.Connection, account_id: UUID, run_date: date
) -> Row | None:
    """The account's earliest failed run before `run_date` (its unresolved failure), or
    None. A blocked row always waits for it."""
    return fetch_one(
        conn,
        """
        SELECT o.run_id, r.run_date FROM billing_run_account o
          JOIN billing_run r ON r.id = o.run_id
         WHERE o.billing_account_id = %s AND o.status = 'failed' AND r.run_date < %s
         ORDER BY r.run_date LIMIT 1
        """,
        (account_id, run_date),
    )


def failed_accounts(conn: psycopg.Connection, run_id: UUID) -> list[UUID]:
    rows = fetch_all(
        conn,
        "SELECT billing_account_id FROM billing_run_account"
        " WHERE run_id = %s AND status = 'failed' ORDER BY billing_account_id",
        (run_id,),
    )
    return [row["billing_account_id"] for row in rows]


def blocked_dates_after(conn: psycopg.Connection, account_id: UUID, run_date: date) -> list[date]:
    """The later run dates on which the account is blocked, in order."""
    rows = fetch_all(
        conn,
        """
        SELECT r.run_date FROM billing_run_account o JOIN billing_run r ON r.id = o.run_id
         WHERE o.billing_account_id = %s AND o.status = 'blocked' AND r.run_date > %s
         ORDER BY r.run_date
        """,
        (account_id, run_date),
    )
    return [row["run_date"] for row in rows]


def retarget_blocked(
    conn: psycopg.Connection, account_id: UUID, after: date, waits_for: UUID
) -> None:
    """The account's blocked rows after `after` now wait for `waits_for` (a catch-up that
    stopped at a later failure)."""
    conn.execute(
        """
        UPDATE billing_run_account o SET waits_for_run_id = %s, updated_at = now()
          FROM billing_run r
         WHERE r.id = o.run_id AND o.billing_account_id = %s AND o.status = 'blocked'
           AND r.run_date > %s
        """,
        (waits_for, account_id, after),
    )


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


# --- the account's history, as the admin UI shows it ---


def page_account_charges(
    conn: psycopg.Connection, account_id: UUID, *, offset: int, limit: int
) -> tuple[list[Row], int]:
    """The account's ABRs, newest run first, with the run date and the offering."""
    rows = fetch_all(
        conn,
        """
        SELECT a.*, r.run_date, o.id AS offering_id, o.name AS offering_name
        FROM applied_customer_billing_rate a
        JOIN billing_run r ON r.id = a.billing_run_id
        JOIN product p ON p.id = a.product_id
        JOIN product_offering o ON o.id = p.product_offering_id
        WHERE a.billing_account_id = %s
        ORDER BY r.run_date DESC, lower(a.period) DESC, a.charge_type, a.id DESC
        OFFSET %s LIMIT %s
        """,
        (account_id, offset, limit),
    )
    total = fetch_one(
        conn,
        "SELECT count(*) AS n FROM applied_customer_billing_rate WHERE billing_account_id = %s",
        (account_id,),
    )
    assert total is not None
    return rows, total["n"]


def page_account_transactions(
    conn: psycopg.Connection, account_id: UUID, *, tz: str, offset: int, limit: int
) -> tuple[list[Row], int]:
    """Charges, refunds and payments as one ledger with the running balance after each
    entry, newest first. Signs as in accountBalance: positive = credit to the customer
    (a charge is -amount, a refund - stored negative - is +|amount|, a payment +amount).

    Order: by day (an ABR's run date, a payment's date in `tz`), then ABRs before
    payments on the same day - the run of D happens at the start of D - then by the
    entry's own timestamp and id. The balance after the newest entry is the account
    balance.
    """
    rows = fetch_all(
        conn,
        """
        WITH ledger AS (
            SELECT a.id, CASE a.charge_type WHEN 'recurring' THEN 'charge' ELSE 'refund' END
                       AS kind,
                   -a.amount AS amount, r.run_date AS day, 0 AS rank, a.created_at AS at,
                   o.name AS note, a.period
            FROM applied_customer_billing_rate a
            JOIN billing_run r ON r.id = a.billing_run_id
            JOIN product p ON p.id = a.product_id
            JOIN product_offering o ON o.id = p.product_offering_id
            WHERE a.billing_account_id = %(account)s
            UNION ALL
            SELECT id, 'payment', amount, (received_at AT TIME ZONE %(tz)s)::date, 1,
                   received_at, payment_method, NULL
            FROM payment
            WHERE billing_account_id = %(account)s AND status = 'done'
        ),
        running AS (
            SELECT *, sum(amount) OVER (ORDER BY day, rank, at, id) AS balance_after
            FROM ledger
        )
        SELECT * FROM running
        ORDER BY day DESC, rank DESC, at DESC, id DESC
        OFFSET %(offset)s LIMIT %(limit)s
        """,
        {"account": account_id, "tz": tz, "offset": offset, "limit": limit},
    )
    total = fetch_one(
        conn,
        """
        SELECT (SELECT count(*) FROM applied_customer_billing_rate
                WHERE billing_account_id = %(account)s)
             + (SELECT count(*) FROM payment
                WHERE billing_account_id = %(account)s AND status = 'done') AS n
        """,
        {"account": account_id},
    )
    assert total is not None
    return rows, total["n"]
