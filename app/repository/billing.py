"""Billing runs, applied customer billing rates and customer bills (TMF678).

Only the billing run writes ABRs. The `load_*` / `write_*` functions at the end map rows
to and from the billing core's dataclasses (app/billing/model.py); the core never sees a
row.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg.types.range import Range

from app.billing import settlement
from app.billing.model import (
    Account,
    AppliedTax,
    DateRange,
    Exemption,
    NewAbr,
    NewBill,
    OfferingChange,
    Price,
    Product,
    StoredAbr,
    StoredBill,
    Tax,
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
    return fetch_one(
        conn,
        "SELECT * FROM billing_run WHERE kind = 'daily' AND run_date = %s FOR UPDATE",
        (run_date,),
    )


def reopen_run(conn: psycopg.Connection, run_id: UUID, *, now: datetime, trigger: str) -> Row:
    """A failed run running again: attempt + 1, the failure cleared."""
    run = fetch_one(
        conn,
        """
        UPDATE billing_run
           SET status = 'running', attempt = attempt + 1, trigger = %s, started_at = %s,
               finished_at = NULL, error = NULL, failure_kind = NULL, updated_at = now()
         WHERE kind = 'daily' AND id = %s AND status = 'failed'
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
         WHERE kind = 'daily' AND id = %s AND status = 'running'
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
        WHERE kind = 'daily' AND id = %s AND status = 'running' RETURNING *
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
    if run is None or run["kind"] != "daily" or run["status"] != "running":
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
    return fetch_one(
        conn, "SELECT * FROM billing_run WHERE kind = 'daily' AND run_date = %s", (run_date,)
    )


def get_run_by_id(conn: psycopg.Connection, run_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_run WHERE id = %s", (run_id,))


def get_daily_run_by_id(conn: psycopg.Connection, run_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM billing_run WHERE kind = 'daily' AND id = %s", (run_id,))


def run_payment_due_date(conn: psycopg.Connection, run_id: UUID) -> date | None:
    """When the bills a run issued on its own date are due (one term per run), or None."""
    row = fetch_one(
        conn,
        "SELECT max(b.payment_due_date) AS due FROM customer_bill b"
        " JOIN billing_run r ON r.id = b.billing_run_id"
        " WHERE b.billing_run_id = %s AND b.bill_date = r.run_date",
        (run_id,),
    )
    return row["due"] if row else None


def held_back_by(conn: psycopg.Connection) -> Row | None:
    """The earliest run that failed as a whole (failure_kind 'run'), or None. No later
    date is billed until it succeeds."""
    return fetch_one(
        conn,
        "SELECT * FROM billing_run WHERE kind = 'daily' AND status = 'failed'"
        " AND failure_kind = 'run'"
        " ORDER BY run_date LIMIT 1",
    )


def unfinished_run(conn: psycopg.Connection) -> Row | None:
    """The earliest run still running - left by a worker that was killed or stopped; its
    new owner takes it over."""
    return fetch_one(
        conn,
        "SELECT * FROM billing_run WHERE kind = 'daily' AND status = 'running'"
        " ORDER BY run_date LIMIT 1",
    )


def failed_account_runs(conn: psycopg.Connection) -> list[Row]:
    """Runs that failed because of accounts (failure_kind 'accounts'), oldest first."""
    return fetch_all(
        conn,
        "SELECT * FROM billing_run WHERE kind = 'daily' AND status = 'failed'"
        " AND failure_kind = 'accounts'"
        " ORDER BY run_date",
    )


def latest_run_date(conn: psycopg.Connection) -> date | None:
    row = fetch_one(conn, "SELECT max(run_date) AS latest FROM billing_run WHERE kind = 'daily'")
    return row["latest"] if row else None


_RUN_VIEW = """
    SELECT r.*, m.total, o.ok, o.failed, o.blocked, r.run_date = l.latest AS is_latest
      FROM billing_run r
     CROSS JOIN (SELECT max(run_date) AS latest FROM billing_run WHERE kind = 'daily') l
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
        _RUN_VIEW
        + " WHERE r.kind = 'daily' ORDER BY r.run_date DESC OFFSET %(offset)s LIMIT %(limit)s",
        {"tz": tz or settings.billing_tz, "offset": offset, "limit": limit},
    )
    total = fetch_one(conn, "SELECT count(*) AS n FROM billing_run WHERE kind = 'daily'")
    assert total is not None
    return rows, total["n"]


def run_view(conn: psycopg.Connection, run_id: UUID, tz: str | None = None) -> Row | None:
    return fetch_one(
        conn,
        _RUN_VIEW + " WHERE r.kind = 'daily' AND r.id = %(id)s",
        {"tz": tz or settings.billing_tz, "id": run_id},
    )


def run_charges(conn: psycopg.Connection, run_ids: list[UUID]) -> dict[UUID, list[Row]]:
    """The ABRs each run wrote: count and sum with taxes per currency (refunds are
    negative)."""
    rows = fetch_all(
        conn,
        """
        SELECT billing_run_id, currency, count(*) AS n, sum(amount + tax_amount) AS amount
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
         WHERE r.kind = 'daily' AND o.billing_account_id = %s AND o.status = 'failed'
           AND r.run_date < %s
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
         WHERE r.kind = 'daily' AND o.billing_account_id = %s
           AND o.status = 'blocked' AND r.run_date > %s
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
         WHERE r.kind = 'daily' AND r.id = o.run_id AND o.billing_account_id = %s
           AND o.status = 'blocked' AND r.run_date > %s
        """,
        (waits_for, account_id, after),
    )


# The name a oneTime rate (a transfer fee) bills under: it has no product or offering.
FEE_NAME = "Transfer fee"


def insert_abr(
    conn: psycopg.Connection,
    *,
    product_id: UUID | None,
    billing_account_id: UUID,
    charge_type: str,
    amount: Decimal,
    currency: str,
    period: Range,
    billing_run_id: UUID,
    applied_at: datetime,
    refunds_rate_id: UUID | None = None,
    customer_bill_id: UUID | None = None,
    taxes: tuple[AppliedTax, ...] = (),
    product_offering_id: UUID | None = None,
    balance_action_id: UUID | None = None,
) -> Row:
    """Insert an applied customer billing rate; `applied_at` (TMF678 date) is the injected
    clock of the run's attempt. Overlaps are rejected by the database (EXCLUDE
    constraint) - note that ON CONFLICT with a target cannot catch those. Tax lines are
    written only when there are some (tests insert ABRs into schemas before 0025), the
    offering only when given (else, from 0027, a trigger takes the product's on the
    period's first day), the balance action only when given (a oneTime transfer fee, from
    0047, which has no product)."""
    columns = {
        "product_id": product_id,
        "billing_account_id": billing_account_id,
        "charge_type": charge_type,
        "amount": money(amount),
        "currency": currency,
        "period": period,
        "billing_run_id": billing_run_id,
        "applied_at": applied_at,
        "refunds_rate_id": refunds_rate_id,
        "customer_bill_id": customer_bill_id,
    }
    if product_offering_id is not None:
        columns["product_offering_id"] = product_offering_id
    if balance_action_id is not None:
        columns["balance_action_id"] = balance_action_id
    row = returning(
        conn,
        sql.SQL("INSERT INTO applied_customer_billing_rate ({}) VALUES ({}) RETURNING *").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join([sql.Placeholder()] * len(columns)),
        ),
        tuple(columns.values()),
    )
    if not taxes:
        return row
    return returning(
        conn,
        "UPDATE applied_customer_billing_rate SET applied_tax = %s, tax_amount = %s"
        " WHERE id = %s RETURNING *",
        (
            _jsonb(
                [
                    {"taxCategory": t.category, "taxRate": t.rate, "taxAmount": t.amount}
                    for t in taxes
                ]
            ),
            money(sum((t.amount for t in taxes), Decimal("0.00"))),
            row["id"],
        ),
    )


def _jsonb(value: Any) -> Jsonb:
    from app.api import tmfjson  # lazily: importing app.api loads every API module

    return Jsonb(value, dumps=tmfjson.dumps)


def applied_taxes(row: Row) -> tuple[AppliedTax, ...]:
    """An ABR row's tax lines (none before 0025)."""
    return tuple(
        AppliedTax(t["taxCategory"], Decimal(t["taxRate"]), Decimal(t["taxAmount"]))
        for t in row.get("applied_tax") or ()
    )


def abr_total(row: Row) -> Decimal:
    """What an ABR row adds to the account's debt: its amount with its taxes."""
    return row["amount"] + row.get("tax_amount", Decimal("0.00"))


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
    payment_due_date: date,
    state: str = "new",
    run_type: str = "onCycle",
) -> Row:
    """The category follows from the amount: a negative bill is a credit note."""
    return returning(
        conn,
        """
        INSERT INTO customer_bill
            (billing_account_id, currency, bill_period, bill_date, state, amount_due,
             billing_run_id, payment_due_date, category, run_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            billing_account_id,
            currency,
            bill_period,
            bill_date,
            state,
            money(amount_due),
            billing_run_id,
            payment_due_date,
            "creditNote" if amount_due < 0 else "normal",
            run_type,
        ),
    )


def account_balance(conn: psycopg.Connection, billing_account_id: UUID) -> Decimal:
    """Payments received minus everything charged, taxes included. Refund ABRs (and their
    taxes) are stored negative, so subtracting the sum of all ABRs already credits them.
    Positive = customer in credit.

    There is deliberately no balance column: it is always computed.
    """
    row = fetch_one(conn, f"SELECT {PAYMENTS_LESS_CHARGES} AS balance", {"id": billing_account_id})
    assert row is not None
    if not has_balance_ledger(conn):
        return row["balance"]
    return (
        row["balance"]
        + conn.execute(BALANCE_ACTION_LEDGER, {"id": billing_account_id}).fetchone()[0]
    )


# Ledger L before balance actions: done payments less tax-inclusive ABRs and payouts.
PAYMENTS_LESS_CHARGES = """
    (SELECT coalesce(sum(amount), 0) FROM payment
     WHERE billing_account_id = %(id)s AND status = 'done')
  - (SELECT coalesce(sum(amount + tax_amount), 0) FROM applied_customer_billing_rate
     WHERE billing_account_id = %(id)s)
  - (SELECT coalesce(sum(amount), 0) FROM refund
     WHERE billing_account_id = %(id)s)
"""
# Ledger L entries of balance actions (0045): signed adjustments (also once cancelled, with
# their compensating reversal), reversals, and completed transfers in and out. A top-up's
# ledger entry is its payment; reservations hold funds without changing L.
BALANCE_ACTION_LEDGER = """
    SELECT
        (SELECT coalesce(sum(amount), 0) FROM balance_action WHERE billing_account_id = %(id)s
         AND kind = 'adjustment' AND status IN ('completed', 'cancelled'))
      + (SELECT coalesce(sum(amount), 0) FROM balance_action_reversal
         WHERE billing_account_id = %(id)s)
      + (SELECT coalesce(sum(amount), 0) FROM balance_action
         WHERE receiver_billing_account_id = %(id)s AND kind = 'transfer' AND status = 'completed')
      - (SELECT coalesce(sum(amount), 0) FROM balance_action
         WHERE billing_account_id = %(id)s AND kind = 'transfer' AND status = 'completed')
"""


def has_balance_ledger(conn: psycopg.Connection) -> bool:
    """Whether the schema has balance actions and their sources (0045); older-schema
    migration tests run these repositories before it."""
    return conn.execute("SELECT to_regclass('funding_consumption') IS NOT NULL").fetchone()[0]


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


def running_run(conn: psycopg.Connection, run_id: UUID) -> Row:
    """A run that is still `running` (run date, the attempt's start), locked against a
    concurrent finish."""
    row = fetch_one(
        conn,
        "SELECT run_date, started_at, status FROM billing_run WHERE kind = 'daily'"
        " AND id = %s FOR SHARE",
        (run_id,),
    )
    if row is None or row["status"] != "running":
        raise LookupError(f"billing run {run_id} is not running")
    return row


def load_account(conn: psycopg.Connection, account_id: UUID) -> Account:
    """The account, locked until the transaction ends: two runs billing the same account
    concurrently would both see it unbilled."""
    row = fetch_one(
        conn,
        "SELECT id, currency, bill_cycle_day, details, rating_type FROM billing_account"
        " WHERE id = %s FOR UPDATE",
        (account_id,),
    )
    if row is None:
        raise LookupError(f"billing account {account_id} does not exist")
    return core_account(row)


def core_account(row: Row) -> Account:
    """A billing_account row as the billing core sees it."""
    return Account(
        id=row["id"],
        currency=row["currency"],
        bill_cycle_day=row["bill_cycle_day"],
        exemptions=exemptions(row["details"]),
        rating_type=row["rating_type"],
    )


def price_taxes(details: dict[str, Any]) -> tuple[Tax, ...]:
    """A price's tax items (TMF620 TaxItem, checked on every write: a category and a rate
    in percent)."""
    return tuple(Tax(t["taxCategory"], Decimal(t["taxRate"])) for t in details.get("tax") or ())


def _instant(period: dict[str, Any] | None, end: str) -> datetime | None:
    value = (period or {}).get(end)
    return None if value is None else datetime.fromisoformat(value)


def exemptions(details: dict[str, Any]) -> tuple[Exemption, ...]:
    """The full exemptions of an account's taxExemption certificates: one per
    taxDefinition with a taxType, valid where the certificate and the definition both
    are. A certificate without a typed definition exempts from nothing."""
    out = []
    for certificate in details.get("taxExemption") or ():
        for definition in certificate.get("taxDefinition") or ():
            if not definition.get("taxType"):
                continue
            starts = [
                s
                for s in (
                    _instant(certificate.get("validFor"), "startDateTime"),
                    _instant(definition.get("validFor"), "startDateTime"),
                )
                if s is not None
            ]
            ends = [
                e
                for e in (
                    _instant(certificate.get("validFor"), "endDateTime"),
                    _instant(definition.get("validFor"), "endDateTime"),
                )
                if e is not None
            ]
            out.append(
                Exemption(
                    definition["taxType"],
                    max(starts) if starts else None,
                    min(ends) if ends else None,
                )
            )
    return tuple(out)


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
    changes = offering_changes(conn, [row["id"] for row in rows], visible_before=visible_before)
    return tuple(
        Product(
            id=row["id"],
            offering_id=row["product_offering_id"],
            status=row["status"]
            if row["termination_known"]
            else STATUS_BEFORE_TERMINATION.get(row["status"], row["status"]),
            start_date=row["start_date"],
            termination_date=row["termination_date"] if row["termination_known"] else None,
            changes=changes.get(row["id"], ()),
        )
        for row in rows
    )


def offering_changes(
    conn: psycopg.Connection, product_ids: list[UUID], *, visible_before: datetime | None = None
) -> dict[UUID, tuple[OfferingChange, ...]]:
    """The products' offering changes by day; with `visible_before`, only those whose
    order completed before that instant (what a billing run sees)."""
    rows = fetch_all(
        conn,
        """
        SELECT c.product_id, c.change_date, c.product_offering_id
        FROM product_offering_change c
        JOIN product_order_item i ON i.id = c.changed_by_item_id
        JOIN product_order o ON o.id = i.product_order_id
        WHERE c.product_id = ANY(%(ids)s)
          AND (%(before)s::timestamptz IS NULL OR o.completion_date < %(before)s)
        ORDER BY c.product_id, c.change_date
        """,
        {"ids": product_ids, "before": visible_before},
    )
    out: dict[UUID, tuple[OfferingChange, ...]] = {}
    for row in rows:
        change = OfferingChange(row["change_date"], row["product_offering_id"])
        out[row["product_id"]] = (*out.get(row["product_id"], ()), change)
    return out


def core_product(row: Row, changes: tuple[OfferingChange, ...] = ()) -> Product:
    """A product row as the core sees it, termination included (not for billing runs:
    they see only what was known before their date, load_products)."""
    return Product(
        id=row["id"],
        offering_id=row["product_offering_id"],
        status=row["status"],
        start_date=row["start_date"],
        termination_date=row["termination_date"],
        changes=changes,
    )


def load_recurring_prices(conn: psycopg.Connection, offering_ids: list[UUID]) -> tuple[Price, ...]:
    """Every recurring price attached to these offerings, in any currency. Not
    filtered by time: a price change only takes effect at or after the moment it was made,
    and the core looks prices up at midnights <= as_of."""
    rows = fetch_all(
        conn,
        """
        SELECT id, product_offering_id, amount, currency, valid_for, details,
               recurring_period, recurring_period_length
        FROM product_offering_price
        WHERE product_offering_id = ANY(%s)
          AND price_type = 'recurring'
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
            taxes=price_taxes(row["details"]),
            recurring_period=row["recurring_period"],
            recurring_period_length=row["recurring_period_length"],
        )
        for row in rows
    )


def product_period(conn: psycopg.Connection, row: Row, day: date, cycle_day: int) -> DateRange:
    """The true product charge period for ordering and read views."""
    from app.billing.core import product_charge_period
    from app.config import settings

    changes = offering_changes(conn, [row["id"]]).get(row["id"], ())
    product = core_product(row, changes)
    return product_charge_period(
        product,
        max(day, product.start_date),
        load_recurring_prices(conn, list(product.offering_ids)),
        row["currency"],
        cycle_day,
        ZoneInfo(settings.billing_tz),
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
            taxes=applied_taxes(row),
            offering_id=row["product_offering_id"],
        )
        for row in rows
    )


def load_bills(conn: psycopg.Connection, account_id: UUID) -> tuple[StoredBill, ...]:
    rows = fetch_all(
        conn,
        "SELECT id, bill_period, bill_date FROM customer_bill WHERE billing_account_id = %s"
        " AND run_type = 'onCycle' ORDER BY bill_date",
        (account_id,),
    )
    return tuple(
        StoredBill(id=row["id"], period=_date_range(row["bill_period"]), bill_date=row["bill_date"])
        for row in rows
    )


def write_abr(
    conn: psycopg.Connection, account: Account, run_id: UUID, abr: NewAbr, *, applied_at: datetime
) -> UUID:
    return insert_abr(
        conn,
        product_id=abr.product_id,
        billing_account_id=account.id,
        charge_type=abr.kind,
        amount=abr.amount,
        currency=abr.currency,
        period=_daterange(abr.period),
        billing_run_id=run_id,
        applied_at=applied_at,
        refunds_rate_id=abr.refunds_id,
        taxes=abr.taxes,
        product_offering_id=abr.offering_id,
    )["id"]


def write_bill(
    conn: psycopg.Connection, account: Account, run_id: UUID, bill: NewBill, payment_term_days: int
) -> UUID:
    """Written as `new`, due `payment_term_days` after its bill date (the term in force now;
    a later change of the term never moves it)."""
    return insert_bill(
        conn,
        billing_account_id=account.id,
        currency=bill.currency,
        bill_period=_daterange(bill.period),
        bill_date=bill.bill_date,
        amount_due=bill.amount_due,
        billing_run_id=run_id,
        payment_due_date=bill.bill_date + timedelta(days=payment_term_days),
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


# --- settlement (app/billing/settlement.py): read and written under the account lock ---


def load_settlement(
    conn: psycopg.Connection, account_id: UUID
) -> tuple[tuple[settlement.Bill, ...], tuple[settlement.Payment, ...]]:
    """Every bill of the account with what was applied to it (or, for a credit note, from
    it), and every received payment with what was applied from it. The caller holds the
    billing account's row lock (`load_account`), so nothing moves under it. Debits,
    outgoing transfers and reversals (funding_consumption) count as used too."""
    consumed = (
        ""
        if not has_balance_ledger(conn)
        else (" + coalesce((SELECT sum(amount) FROM funding_consumption WHERE {} = {}.id), 0)")
    )
    bills = fetch_all(
        conn,
        """
        SELECT b.id, b.bill_date, b.amount_due, b.state,
               coalesce((SELECT sum(amount) FROM bill_allocation
                         WHERE customer_bill_id = b.id), 0) AS allocated,
               coalesce((SELECT sum(amount) FROM bill_allocation
                         WHERE credit_bill_id = b.id), 0)
               + coalesce((SELECT sum(amount) FROM refund_source
                           WHERE credit_bill_id = b.id), 0){credit_consumed} AS credit_used,
               coalesce((SELECT sum(s.amount) FROM funding_hold_source s
                         JOIN funding_hold h ON h.id = s.hold_id
                         WHERE s.credit_bill_id = b.id AND h.released_at IS NULL), 0) AS held
        FROM customer_bill b WHERE b.billing_account_id = %s ORDER BY b.bill_date
        """.replace("{credit_consumed}", consumed.format("credit_bill_id", "b")),
        (account_id,),
    )
    payments = fetch_all(
        conn,
        """
        SELECT p.id, p.received_at, p.amount,
               coalesce((SELECT sum(amount) FROM bill_allocation
                         WHERE payment_id = p.id), 0)
               + coalesce((SELECT sum(amount) FROM refund_source
                           WHERE payment_id = p.id), 0){payment_consumed} AS used,
               coalesce((SELECT sum(s.amount) FROM funding_hold_source s
                         JOIN funding_hold h ON h.id = s.hold_id
                         WHERE s.payment_id = p.id AND h.released_at IS NULL), 0) AS held
        FROM payment p WHERE p.billing_account_id = %s AND p.status = 'done'
        ORDER BY p.received_at, p.id
        """.replace("{payment_consumed}", consumed.format("payment_id", "p")),
        (account_id,),
    )
    return (
        tuple(
            settlement.Bill(
                id=r["id"],
                bill_date=r["bill_date"],
                amount_due=r["amount_due"],
                state=r["state"],
                allocated=r["allocated"],
                credit_used=r["credit_used"],
                held=r["held"],
            )
            for r in bills
        ),
        tuple(
            settlement.Payment(
                id=r["id"],
                received_at=r["received_at"],
                amount=r["amount"],
                used=r["used"],
                held=r["held"],
            )
            for r in payments
        ),
    )


def write_settlement(
    conn: psycopg.Connection,
    account: Account,
    result: settlement.Settlement,
    *,
    now: datetime,
    cause: str,
    operator: str | None = None,
) -> None:
    """Insert the allocations, move the bills to their new states with one history row
    each, and touch every bill that was paid (lastUpdate moves even when its state does
    not). The amount bounds are checked by the database at commit."""
    for a in result.allocations:
        conn.execute(
            """
            INSERT INTO bill_allocation
                (billing_account_id, currency, customer_bill_id, payment_id, credit_bill_id,
                 amount, kind, allocated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                account.id,
                account.currency,
                a.bill_id,
                a.payment_id,
                a.credit_bill_id,
                money(a.amount),
                a.kind,
                now,
            ),
        )
    for change in result.changes:
        record_bill_state(conn, change, now=now, cause=cause, operator=operator)
    touched = {a.bill_id for a in result.allocations} - {c.bill_id for c in result.changes}
    if touched:
        conn.execute(
            "UPDATE customer_bill SET updated_at = now() WHERE id = ANY(%s)", (list(touched),)
        )
    if result.allocations:
        # check the amount bounds here, inside the caller's savepoint, not at the far
        # commit: a breach is a bug and should fail where it happens
        conn.execute("SET CONSTRAINTS bill_allocation_bounds IMMEDIATE")


def record_bill_state(
    conn: psycopg.Connection,
    change: settlement.StateChange,
    *,
    now: datetime,
    cause: str,
    operator: str | None = None,
    notify: bool = True,
) -> None:
    """Move one bill from `change.from_state` to `change.to_state` and record it. Raises
    LookupError if the bill is no longer in `from_state`. `notify`: emit the TMF678
    StateChange event (issuing emits one Create for its steps instead)."""
    row = fetch_one(
        conn,
        "UPDATE customer_bill SET state = %s WHERE id = %s AND state = %s RETURNING id",
        (change.to_state, change.bill_id, change.from_state),
    )
    if row is None:
        raise LookupError(f"bill {change.bill_id} is not {change.from_state}")
    conn.execute(
        """
        INSERT INTO customer_bill_state_change
            (customer_bill_id, from_state, to_state, changed_at, cause, operator)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (change.bill_id, change.from_state, change.to_state, now, cause, operator),
    )
    if notify:
        from app.events import emit  # the API bodies import this module

        emit.bill(conn, change.bill_id, "StateChange", at=now)


def record_bill_issue(conn: psycopg.Connection, bill_id: UUID, *, now: datetime) -> None:
    """The first history row of a bill: issued as `new` by a billing run."""
    conn.execute(
        """
        INSERT INTO customer_bill_state_change
            (customer_bill_id, from_state, to_state, changed_at, cause)
        VALUES (%s, NULL, 'new', %s, 'run')
        """,
        (bill_id, now),
    )


def load_issue_candidates(
    conn: psycopg.Connection, account_id: UUID
) -> tuple[settlement.IssueCandidate, ...]:
    """The account's bills not issued yet (`new`, or `validated` if a run ever stopped
    between the two), oldest first, with the sum and number of the ABRs they collect."""
    rows = fetch_all(
        conn,
        """
        SELECT b.id, b.bill_date, b.bill_period, b.amount_due, b.state, b.run_type,
               coalesce(sum(a.amount + a.tax_amount), 0) AS abr_total, count(a.id) AS abr_count
        FROM customer_bill b
        LEFT JOIN applied_customer_billing_rate a ON a.customer_bill_id = b.id
        WHERE b.billing_account_id = %s AND b.state IN ('new', 'validated')
        GROUP BY b.id ORDER BY b.bill_date
        """,
        (account_id,),
    )
    return tuple(
        settlement.IssueCandidate(
            id=r["id"],
            bill_date=r["bill_date"],
            period_start=r["bill_period"].lower,
            period_end=r["bill_period"].upper,
            amount_due=r["amount_due"],
            state=r["state"],
            abr_total=r["abr_total"],
            abr_count=r["abr_count"],
            on_demand=r["run_type"] == "offCycle",
        )
        for r in rows
    )


def lock_bill(conn: psycopg.Connection, bill_id: UUID) -> Row | None:
    """The bill, locked. Take the billing account's lock first (`load_account`), as every
    writer of allocations does, so nobody waits in the other order."""
    return fetch_one(conn, "SELECT * FROM customer_bill WHERE id = %s FOR UPDATE", (bill_id,))


def bill_allocated(conn: psycopg.Connection, bill_id: UUID) -> Decimal:
    row = fetch_one(
        conn,
        "SELECT coalesce(sum(amount), 0) AS total FROM bill_allocation WHERE customer_bill_id = %s",
        (bill_id,),
    )
    assert row is not None
    return row["total"]


def remaining_amount(bill: Row, allocated: Decimal) -> Decimal:
    """TMF678 remainingAmount: what is still owed on a bill; nothing on a credit note."""
    return max(bill["amount_due"] - allocated, Decimal("0.00"))


def applied_payments(conn: psycopg.Connection, bill_id: UUID) -> list[Row]:
    """Per payment, what it paid on this bill (TMF678 appliedPayment), oldest first."""
    return fetch_all(
        conn,
        """
        SELECT payment_id, sum(amount) AS amount FROM bill_allocation
        WHERE customer_bill_id = %s AND payment_id IS NOT NULL
        GROUP BY payment_id ORDER BY min(allocated_at), payment_id
        """,
        (bill_id,),
    )


def payment_items(conn: psycopg.Connection, payment_id: UUID) -> list[Row]:
    """Per bill, what this payment paid on it (TMF676 paymentItem), oldest bill first."""
    return fetch_all(
        conn,
        """
        SELECT a.customer_bill_id, sum(a.amount) AS amount
        FROM bill_allocation a JOIN customer_bill b ON b.id = a.customer_bill_id
        WHERE a.payment_id = %s
        GROUP BY a.customer_bill_id, b.bill_date ORDER BY b.bill_date
        """,
        (payment_id,),
    )


def bill_sources(conn: psycopg.Connection, bill_id: UUID) -> list[Row]:
    """Every allocation to this bill with its source (payment or credit note), in the
    order they were made."""
    return fetch_all(
        conn,
        """
        SELECT a.amount, a.kind, a.allocated_at, a.payment_id, a.credit_bill_id,
               p.payment_method, p.received_at, c.bill_date AS credit_bill_date
        FROM bill_allocation a
        LEFT JOIN payment p ON p.id = a.payment_id
        LEFT JOIN customer_bill c ON c.id = a.credit_bill_id
        WHERE a.customer_bill_id = %s
        ORDER BY a.allocated_at, a.created_at, a.id
        """,
        (bill_id,),
    )


def bill_charges(conn: psycopg.Connection, bill_id: UUID) -> list[Row]:
    """The ABRs a bill collected, with their offering, oldest period first."""
    return fetch_all(
        conn,
        """
        SELECT a.*, o.id AS offering_id, o.name AS offering_name
        FROM applied_customer_billing_rate a
        LEFT JOIN product_offering o ON o.id = a.product_offering_id
        WHERE a.customer_bill_id = %s
        ORDER BY lower(a.period), a.charge_type, a.id
        """,
        (bill_id,),
    )


def bill_taxes(conn: psycopg.Connection, bill_id: UUID) -> tuple[Decimal, list[Row]]:
    """A bill's amount before tax (the sum of its ABRs' amounts) and its tax items: the
    ABRs' tax lines summed per category and rate (TMF678 taxItem), not rounded again."""
    net = fetch_one(
        conn,
        "SELECT coalesce(sum(amount), 0) AS net FROM applied_customer_billing_rate"
        " WHERE customer_bill_id = %s",
        (bill_id,),
    )
    assert net is not None
    lines = fetch_all(
        conn,
        """
        SELECT t->>'taxCategory' AS category, (t->>'taxRate')::numeric AS rate,
               sum((t->>'taxAmount')::numeric) AS amount
        FROM applied_customer_billing_rate a, jsonb_array_elements(a.applied_tax) t
        WHERE a.customer_bill_id = %s
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (bill_id,),
    )
    return net["net"], lines


def settlement_summary(conn: psycopg.Connection, account_id: UUID, today: date) -> Row:
    """What the account owes on its bills and the credit it has not used yet. Overdue:
    due before `today` with something remaining (on hold included - shown apart)."""
    row = fetch_one(
        conn,
        """
        WITH bills AS (
            SELECT b.state, b.payment_due_date,
                   CASE WHEN b.amount_due > 0 THEN b.amount_due - coalesce(
                       (SELECT sum(amount) FROM bill_allocation
                        WHERE customer_bill_id = b.id), 0) ELSE 0 END AS remaining,
                   CASE WHEN b.amount_due < 0 AND b.state = 'settled' THEN -b.amount_due
                       - coalesce((SELECT sum(amount) FROM bill_allocation
                                   WHERE credit_bill_id = b.id), 0)
                       - coalesce((SELECT sum(amount) FROM refund_source
                                   WHERE credit_bill_id = b.id), 0) ELSE 0 END AS credit_left
            FROM customer_bill b WHERE b.billing_account_id = %(account)s
        )
        SELECT
            coalesce(sum(remaining), 0) AS outstanding,
            count(*) FILTER (WHERE remaining > 0 AND payment_due_date < %(today)s)
                AS overdue_count,
            coalesce(sum(remaining) FILTER (WHERE payment_due_date < %(today)s), 0)
                AS overdue_amount,
            count(*) FILTER (WHERE state = 'onHold') AS on_hold_count,
            coalesce(sum(remaining) FILTER (WHERE state = 'onHold'), 0) AS on_hold_amount,
            coalesce(sum(credit_left), 0)
              + (SELECT coalesce(sum(p.amount - coalesce((SELECT sum(amount)
                         FROM bill_allocation WHERE payment_id = p.id), 0)
                         - coalesce((SELECT sum(amount) FROM refund_source
                                     WHERE payment_id = p.id), 0)), 0)
                 FROM payment p
                 WHERE p.billing_account_id = %(account)s AND p.status = 'done')
                AS unallocated_credit
        FROM bills
        """,
        {"account": account_id, "today": today},
    )
    assert row is not None
    return row


def bill_state_history(conn: psycopg.Connection, bill_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM customer_bill_state_change WHERE customer_bill_id = %s ORDER BY seq",
        (bill_id,),
    )


# --- the account's history, as the admin UI shows it ---


def page_account_charges(
    conn: psycopg.Connection, account_id: UUID, *, offset: int, limit: int
) -> tuple[list[Row], int]:
    """The account's ABRs, newest run first, with the run date and the offering."""
    rows = fetch_all(
        conn,
        """
        SELECT a.*, r.run_date, o.id AS offering_id, o.name AS offering_name,
               lower(coalesce(c.period, a.period)) AS charge_start
        FROM applied_customer_billing_rate a
        LEFT JOIN applied_customer_billing_rate c ON c.id = a.refunds_rate_id
        JOIN billing_run r ON r.id = a.billing_run_id
        LEFT JOIN product_offering o ON o.id = a.product_offering_id
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
    (a charge is -amount, a refund - stored negative - is +|amount|, a payment +amount;
    an ABR's amount here includes its taxes).

    Order: by day (an ABR's run date, a payment's date in `tz`), then ABRs before
    payments on the same day - the run of D happens at the start of D - then by the
    entry's own timestamp and id. The balance after the newest entry is the account
    balance.
    """
    rows = fetch_all(
        conn,
        """
        WITH ledger AS (
            SELECT a.id, CASE a.charge_type WHEN 'refund' THEN 'refund' ELSE 'charge' END
                       AS kind,
                   -(a.amount + a.tax_amount) AS amount, r.run_date AS day, 0 AS rank,
                   a.applied_at AS at,
                   coalesce(o.name, %(fee)s) AS note, a.period
            FROM applied_customer_billing_rate a
            JOIN billing_run r ON r.id = a.billing_run_id
            LEFT JOIN product_offering o ON o.id = a.product_offering_id
            WHERE a.billing_account_id = %(account)s
            UNION ALL
            SELECT id, 'payment', amount, (received_at AT TIME ZONE %(tz)s)::date, 1,
                   received_at, payment_method, NULL
            FROM payment
            WHERE billing_account_id = %(account)s AND status = 'done'
            UNION ALL
            SELECT id, 'payout', -amount, (refunded_at AT TIME ZONE %(tz)s)::date, 1,
                   refunded_at, coalesce(method_name, method_type), NULL
            FROM refund WHERE billing_account_id = %(account)s
        ),
        running AS (
            SELECT *, sum(amount) OVER (
                ORDER BY day, rank, at, (kind = 'payout'), id
            ) AS balance_after
            FROM ledger
        )
        SELECT * FROM running
        ORDER BY day DESC, rank DESC, at DESC, (kind = 'payout') DESC, id DESC
        OFFSET %(offset)s LIMIT %(limit)s
        """,
        {"account": account_id, "tz": tz, "fee": FEE_NAME, "offset": offset, "limit": limit},
    )
    total = fetch_one(
        conn,
        """
        SELECT (SELECT count(*) FROM applied_customer_billing_rate
                WHERE billing_account_id = %(account)s)
             + (SELECT count(*) FROM payment
                WHERE billing_account_id = %(account)s AND status = 'done')
             + (SELECT count(*) FROM refund WHERE billing_account_id = %(account)s) AS n
        """,
        {"account": account_id},
    )
    assert total is not None
    return rows, total["n"]
