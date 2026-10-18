"""Runs the billing core against the database for one account.

The stage 4 worker calls `bill_account` once per account inside a billing run. Everything
the core needs is loaded as dataclasses through the repository, and everything it returns
is written in one transaction (a savepoint when the caller already has one open): an
account is billed completely for the run date, or not at all.
"""

from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import settlement
from app.billing.core import add_month, compute, cycle_period
from app.billing.model import Account, BillingResult, BillingState
from app.config import settings
from app.events import emit
from app.repository import billing, presentation


def visible_before(run_date: date, tz: ZoneInfo) -> datetime:
    """The run for D sees order-driven facts completed before 00:00 of D in BILLING_TZ:
    end-of-day processing of D-1. The run's own wall-clock time never matters, so a run
    replayed after an outage sees exactly what the daily run would have seen."""
    return datetime.combine(run_date, time.min, tzinfo=tz)


def load_state(
    conn: psycopg.Connection, account_id: UUID, run_date: date, tz: ZoneInfo
) -> BillingState:
    account = billing.load_account(conn, account_id)
    products = billing.load_products(conn, account_id, visible_before=visible_before(run_date, tz))
    return BillingState(
        account=account,
        products=products,
        prices=billing.load_recurring_prices(
            conn, [offering for p in products for offering in p.offering_ids]
        ),
        abrs=billing.load_abrs(conn, account_id),
        bills=billing.load_bills(conn, account_id),
    )


def apply_result(
    conn: psycopg.Connection,
    account: Account,
    run_id: UUID,
    result: BillingResult,
    *,
    issued_at: datetime,
) -> None:
    """Write the ABRs and bills, both dated `issued_at` (the injected clock of the run's
    attempt): the ABRs' applied_at, and each bill's issue row in the state history."""
    ids = {
        abr.key: billing.write_abr(conn, account, run_id, abr, applied_at=issued_at)
        for abr in result.abrs
    }
    for bill in result.bills:
        bill_id = billing.write_bill(conn, account, run_id, bill, settings.bill_payment_term_days)
        billing.record_bill_issue(conn, bill_id, now=issued_at)
        members = [*bill.abr_ids, *(ids[key] for key in bill.abr_keys)]
        billing.attach_abrs_to_bill(conn, bill_id, account.id, members)


def issue_bills(conn: psycopg.Connection, account: Account, *, now: datetime) -> None:
    """Issue every bill of the account still waiting (written by this run, or `new` from
    before migration 0006): validate it, then new -> validated -> sent (-> settled when
    nothing is due). A bill that fails validation raises InconsistentState, so the account
    fails in the run and nothing of it is written."""
    for candidate in billing.load_issue_candidates(conn, account.id):
        end = (
            cycle_period(candidate.bill_date, account.bill_cycle_day).end
            if candidate.on_demand
            else add_month(candidate.bill_date)
        )
        settlement.validate(candidate, end)
        presentation.snapshot_bill(conn, candidate.id, now=now)
        for change in settlement.issue(candidate):
            billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
        # one event for the bill as issued: its new -> validated -> sent steps are internal
        emit.bill(conn, candidate.id, "Create", at=now)


def settle_account(
    conn: psycopg.Connection,
    account: Account,
    *,
    now: datetime,
    cause: str,
    explicit: tuple[settlement.ExplicitItem, ...] = (),
    explicit_payment_id: UUID | None = None,
) -> settlement.Settlement:
    """Apply the account's unused credit to its open bills and write the result. The
    caller holds the billing account's row lock (`billing.load_account`)."""
    bills, payments = billing.load_settlement(conn, account.id)
    result = settlement.settle(bills, payments, explicit, explicit_payment_id)
    billing.write_settlement(conn, account, result, now=now, cause=cause)
    return result


def bill_account(
    conn: psycopg.Connection, account_id: UUID, run: dict, *, tz: ZoneInfo | None = None
) -> BillingResult:
    """Bill one account for `run` (a billing_run row that is still running): write the
    ABRs and bills due up to its run date, issue the bills, and settle the account (its
    unused credit pays its open bills). Running it again for the same run writes nothing.
    Raises BillingError (nothing written) if the account cannot be billed."""
    tz = tz or ZoneInfo(settings.billing_tz)
    with conn.transaction():
        current = billing.running_run(conn, run["id"])
        run_date = current["run_date"]
        state = load_state(conn, account_id, run_date, tz)
        result = compute(state, run_date, tz)
        now = current["started_at"]
        apply_result(conn, state.account, run["id"], result, issued_at=now)
        issue_bills(conn, state.account, now=now)
        settle_account(conn, state.account, now=now, cause="run")
    return result
