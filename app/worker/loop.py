"""One wake-up of the billing worker (docs/archive/stage4-design.md sections 4 and 6).

The worker wakes at least every minute, re-reads the clock and does whatever is due -
the same decision whether the date just turned, the machine was suspended, or the worker
was down (the 03:00 reboot, a deploy):

1. ownership: if another live worker owns billing, back off;
2. the dates due on their own (plan.scheduled: today from BILLING_RUN_AT, yesterday, a
   run that failed as a whole; an older gap is reported and left to an operator);
3. before a new scheduled date, the runs that failed because of accounts are retried
   once (D-W17) - the operator's retry is for immediacy;
4. those dates, in order, with the scheduled trigger;
5. the UI's queued requests, then prepaid work: due renewals are produced, retained
   commands dispatched and entitlement transitions observed (app/worker/prepaid.py), then
   the online signups' steps (app/worker/signup.py);
6. postpaid dunning (app/worker/dunning.py): the steps on the full sweep of a business date,
   the restorations on every tick;
7. the customer notifications (app/worker/notify.py): events found, messages sent;
8. the due event deliveries to hub callbacks (app/events/deliver.py);
9. from 04:00 to 05:30, the housekeeping (app/worker/housekeeping.py): usage_record
   partitions and the deletion of rows past their retention, never while a run is running.

A `checkpoint` (SIGTERM, liveness) is honoured between accounts and between requests;
Stopped leaves the run in hand running for the next worker. The `heartbeat` (liveness
only, never a stop) is called after every account of every path, retries included.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, time
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.events import deliver
from app.repository import account_review, billing
from app.worker.dunning import process_dunning
from app.worker.housekeeping import Housekeeping, housekeep
from app.worker.lifecycle import process_lifecycle
from app.worker.notify import process_notifications
from app.worker.plan import scheduled
from app.worker.prepaid import observe_entitlements, process_commands, produce_renewals
from app.worker.process import (
    Checkpoint,
    Heartbeat,
    HeldBack,
    NotYet,
    RunAborted,
    Stopped,
    bill_date,
    retry_run,
)
from app.worker.queue import process_requests
from app.worker.runs import RunInProgress, ensure_ownership
from app.worker.signup import process_signups

log = logging.getLogger("app.worker")


@dataclass
class Tick:
    """What one wake-up did."""

    backed_off: bool = False
    gap: tuple[date, date] | None = None
    retried: list[date] = field(default_factory=list)
    billed: list[date] = field(default_factory=list)
    requests: list[UUID] = field(default_factory=list)
    prepaid_renewals: list[UUID] = field(default_factory=list)
    prepaid_commands: list[UUID] = field(default_factory=list)
    signups: list[UUID] = field(default_factory=list)  # online signups acted on (#377)
    dunning: list[tuple[UUID, str]] = field(default_factory=list)  # (account, step) taken (#385)
    deliveries: int = 0  # event POSTs tried (step 8)
    reviewed: int | None = None  # accounts the per-account passes looked at; None: all
    housekeeping: Housekeeping | None = None  # step 9; None when a stop came before it
    stopped: bool = False


def tick(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    run_at: time,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> Tick:
    result = Tick()
    try:
        ensure_ownership(conn)
    except RunInProgress:
        log.info("another worker owns billing; backing off")
        result.backed_off = True
        return result
    held = billing.held_back_by(conn)
    unfinished = billing.unfinished_run(conn)  # we own billing: its worker is gone
    plan = scheduled(
        billing.latest_run_date(conn),
        held["run_date"] if held else None,
        clock.now().astimezone(tz),
        run_at,
        unfinished["run_date"] if unfinished else None,
    )
    result.gap = plan.gap
    if plan.gap is not None:
        log.warning(
            "billing dates %s to %s were missed: not billed automatically, catch up from the"
            " admin UI",
            *plan.gap,
        )
    try:
        if plan.dates and held is None:  # a new scheduled date: retry failed accounts first
            for run in billing.failed_account_runs(conn):
                # re-read: retrying an earlier run catches its accounts up on this one too
                current = billing.get_daily_run_by_id(conn, run["id"])
                if current is None or current["status"] != "failed":
                    continue
                retry_run(conn, run["id"], clock=clock, tz=tz, heartbeat=heartbeat)
                result.retried.append(run["run_date"])
        for run_date in plan.dates:
            try:
                bill_date(
                    conn,
                    run_date,
                    clock=clock,
                    tz=tz,
                    trigger="scheduled",
                    checkpoint=checkpoint,
                    heartbeat=heartbeat,
                )
            except (RunAborted, HeldBack, NotYet) as stop:
                log.warning("billing %s: %s", run_date, stop)
                break
            result.billed.append(run_date)
        result.requests = process_requests(
            conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
        )
        result.prepaid_renewals = produce_renewals(
            conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
        )
        # the per-account passes look at every prepaid account once per business date and
        # otherwise only at the accounts whose money or products changed (#292)
        review = account_review.begin(conn, clock.now().astimezone(tz).date())
        result.reviewed = None if review.full else len(review.marks)
        process_lifecycle(
            conn,
            clock=clock,
            tz=tz,
            checkpoint=checkpoint,
            heartbeat=heartbeat,
            accounts=review.accounts,
        )
        result.prepaid_commands = process_commands(
            conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
        )
        # after the payments and the network: a confirmed top-up or activation is seen at once
        result.signups = process_signups(
            conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
        )
        observe_entitlements(
            conn,
            clock=clock,
            tz=tz,
            checkpoint=checkpoint,
            heartbeat=heartbeat,
            accounts=review.accounts,
        )
        # after billing and payments, before the notifications that tell the steps
        result.dunning = process_dunning(
            conn,
            clock=clock,
            tz=tz,
            full=review.full,
            checkpoint=checkpoint,
            heartbeat=heartbeat,
        )
        process_notifications(
            conn,
            clock=clock,
            tz=tz,
            checkpoint=checkpoint,
            heartbeat=heartbeat,
            accounts=review.accounts,
        )
        if checkpoint is None or not checkpoint():  # a stopped pass is evaluated again
            account_review.finish(conn, review)
        sent = deliver.process(conn, clock=clock, checkpoint=checkpoint, heartbeat=heartbeat)
        result.deliveries = len(sent.delivered) + len(sent.retrying) + len(sent.failed)
        result.housekeeping = housekeep(
            conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
        )
    except Stopped as stopped:
        log.info("%s", stopped)
        result.stopped = True
    return result
