"""Billing one date, account by account, and retrying what failed
(docs/stage4-design.md, section 3; the stage 4 planning decisions in CLAUDE.md).

Per account, one transaction (the account's charges and its outcome row commit together
or not at all - bill_account's own transaction is a savepoint inside it):

    an unresolved earlier failure     -> blocked, waiting for that run; nothing billed
    bill_account succeeds             -> ok
    bill_account raises BillingError  -> failed, with the error; nothing billed

Anything else - the database gone, LockNotAvailable (a row lock held past lock_timeout:
transient contention, not the account's data), a bug - leaves the account without an
outcome and ends the run: a whole-run failure. bill_date marks the run failed with
failure_kind 'run' if the connection still works (a dead one cannot: the run stays
running, its lock is gone with the connection, and the next owner takes it over). No
later date is billed while a run failed as a whole (HeldBack), and the scheduled retry of
that date waits for its backoff (NotYet, app/worker/plan.py).

A run is completed only when every member account is ok (billing.finish_run checks it);
otherwise it is failed with failure_kind 'accounts' and the worker moves on to later
dates. A failed account is not billed for later dates (blocked) until a retry fixes it;
then it is billed for its dates in order, each in its own run, reopening one run at a
time (Q2), so it ends with exactly the rows a run without the failure would have written.
"""

from collections import deque
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing.adapter import bill_account, visible_before
from app.billing.model import BillingError
from app.clock import Clock
from app.repository import billing, products
from app.repository._sql import Row
from app.worker.plan import retry_time
from app.worker.runs import AlreadyCompleted, Started, start_run


class HeldBack(Exception):
    """A later date while an earlier run failed as a whole: that run comes first."""

    def __init__(self, run: Row) -> None:
        super().__init__(f"billing run {run['run_date']} failed as a whole; it comes first")
        self.run = run


class NotYet(Exception):
    """The scheduled retry of a run that failed as a whole, before its backoff."""

    def __init__(self, run: Row, due: datetime) -> None:
        super().__init__(f"billing run {run['run_date']} may be retried from {due.isoformat()}")
        self.run, self.due = run, due


class RunAborted(Exception):
    """A whole-run failure. `marked`: the run was set failed ('run'); False when the
    connection was gone (the run stays running for a takeover)."""

    def __init__(self, run: Row, cause: BaseException, marked: bool) -> None:
        super().__init__(f"billing run {run['run_date']} aborted: {type(cause).__name__}: {cause}")
        self.run, self.cause, self.marked = run, cause, marked


class DateRun:
    """One billing run, processed one account per step(): the caller (a test, the SIGTERM
    handling of slice d) may stop between accounts. `only` limits it to some accounts (a
    retry); accounts already ok in this run are never processed again."""

    def __init__(
        self,
        conn: psycopg.Connection,
        run: Row,
        *,
        clock: Clock,
        tz: ZoneInfo,
        only: list[UUID] | None = None,
    ) -> None:
        self.conn, self.run, self.clock, self.tz = conn, run, clock, tz
        self.run_date: date = run["run_date"]
        done = billing.outcomes(conn, run["id"])
        accounts = billing.run_members(conn, self.run_date, tz.key) if only is None else only
        self.todo = deque(a for a in accounts if a not in done or done[a]["status"] != "ok")

    def step(self) -> bool:
        """Process the next account; False when there is none left."""
        if not self.todo:
            return False
        self.process(self.todo.popleft())
        return True

    def run_all(self) -> None:
        while self.step():
            pass

    def process(self, account_id: UUID) -> None:
        conn, run_id, now = self.conn, self.run["id"], self.clock.now()
        blocker = billing.earliest_failure_before(conn, account_id, self.run_date)
        with conn.transaction():
            if blocker is not None:
                billing.record_outcome(
                    conn, run_id, account_id, "blocked", now=now, waits_for=blocker["run_id"]
                )
            else:
                try:
                    bill_account(conn, account_id, self.run, tz=self.tz)
                except BillingError as error:
                    billing.record_outcome(
                        conn,
                        run_id,
                        account_id,
                        "failed",
                        now=now,
                        error=f"{type(error).__name__}: {error}",
                    )
                else:
                    billing.record_outcome(conn, run_id, account_id, "ok", now=now)
            billing.touch_run(conn, run_id, now)

    def close(self) -> Row:
        return close_run(self.conn, self.run, clock=self.clock, tz=self.tz)


def close_run(conn: psycopg.Connection, run: Row, *, clock: Clock, tz: ZoneInfo) -> Row:
    """Completed when every member is ok, else failed because of accounts."""
    with conn.transaction():
        gaps = billing.outcome_gaps(conn, run["id"], run["run_date"], tz.key)
        if not gaps:
            return billing.finish_run(conn, run["id"], now=clock.now(), tz=tz.key)
        total = len(billing.run_members(conn, run["run_date"], tz.key))
        detail = ", ".join(f"{n} {status}" for status, n in sorted(gaps.items()))
        return billing.fail_run(
            conn,
            run["id"],
            now=clock.now(),
            error=f"{sum(gaps.values())} of {total} accounts not billed ({detail})",
            failure_kind="accounts",
        )


def bill_date(
    conn: psycopg.Connection, run_date: date, *, clock: Clock, tz: ZoneInfo, trigger: str
) -> Row:
    """The billing run of one date, start to end (a completed run is left alone).

    Refuses a date after a run that failed as a whole (HeldBack), and - for the scheduled
    trigger - the retry of such a run before its backoff (NotYet); an operator's catch-up
    or retry is not held to the backoff. A whole-run failure raises RunAborted."""
    held = billing.held_back_by(conn)
    if held is not None and held["run_date"] < run_date:
        raise HeldBack(held)
    if held is not None and held["run_date"] == run_date and trigger == "scheduled":
        due = retry_time(held)
        if clock.now() < due:
            raise NotYet(held, due)
    started = start_run(conn, run_date, now=clock.now(), trigger=trigger)
    if isinstance(started, AlreadyCompleted):
        return started.run
    date_run = DateRun(conn, started.run, clock=clock, tz=tz)
    try:
        flip_statuses(conn, run_date, tz)
        date_run.run_all()
        return date_run.close()
    except Exception as error:
        raise RunAborted(
            started.run, error, _mark_failed(conn, started.run, error, clock)
        ) from error


def flip_statuses(conn: psycopg.Connection, run_date: date, tz: ZoneInfo) -> dict[str, int]:
    """The status flips at the start of the run of `run_date`, in one transaction, before
    billing (section 5): by dates, for what the run sees, blocked accounts included.
    Cosmetic for money - billing is driven by dates - but visible in TMF637 and the UI."""
    with conn.transaction():
        return products.flip_statuses(conn, run_date, visible_before=visible_before(run_date, tz))


def _mark_failed(conn: psycopg.Connection, run: Row, error: Exception, clock: Clock) -> bool:
    """Set the run failed as a whole; False if the connection cannot do it any more."""
    try:
        with conn.transaction():
            billing.fail_run(
                conn,
                run["id"],
                now=clock.now(),
                error=f"{type(error).__name__}: {error}",
                failure_kind="run",
            )
    except psycopg.Error:
        return False
    return True


def _redo(
    conn: psycopg.Connection, run_date: date, accounts: list[UUID], *, clock: Clock, tz: ZoneInfo
) -> dict[UUID, str]:
    """Reopen the failed run of `run_date`, process `accounts` only, close it again; the
    accounts' outcomes."""
    started = start_run(conn, run_date, now=clock.now(), trigger="retry")
    if not isinstance(started, Started):
        raise LookupError(f"billing run {run_date} is not failed")
    date_run = DateRun(conn, started.run, clock=clock, tz=tz, only=accounts)
    date_run.run_all()
    date_run.close()
    done = billing.outcomes(conn, started.run["id"])
    return {a: done[a]["status"] for a in accounts}


def retry_run(conn: psycopg.Connection, run_id: UUID, *, clock: Clock, tz: ZoneInfo) -> None:
    """Retry a failed run: only its failed accounts (accounts blocked on an earlier date
    stay blocked until that date is fixed). An account that succeeds is then billed for
    its blocked later dates, in order, one run at a time; it stops at the first date that
    fails again, and its later blocked rows wait for that one instead."""
    run = billing.get_run_by_id(conn, run_id)
    if run is None or run["status"] != "failed":
        raise LookupError(f"billing run {run_id} is not failed")
    accounts = billing.failed_accounts(conn, run_id)
    if not accounts:
        return  # failed because of blocked accounts only: their earlier dates come first
    first = _redo(conn, run["run_date"], accounts, clock=clock, tz=tz)
    active = {a for a, status in first.items() if status == "ok"}
    blocked = {a: billing.blocked_dates_after(conn, a, run["run_date"]) for a in active}
    for run_date in sorted({d for dates in blocked.values() for d in dates}):
        todo = sorted(a for a in active if run_date in blocked[a])
        if not todo:
            continue
        results = _redo(conn, run_date, todo, clock=clock, tz=tz)
        stopped = billing.get_run(conn, run_date)
        for account_id, status in results.items():
            if status != "ok":
                billing.retarget_blocked(conn, account_id, run_date, stopped["id"])
                active.discard(account_id)
