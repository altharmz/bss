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
5. the UI's queued requests;
6. the due event deliveries to hub callbacks (app/events/deliver.py), then the purge of
   events delivered more than 30 days ago.

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
from app.repository import billing
from app.worker.plan import scheduled
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

log = logging.getLogger("app.worker")


@dataclass
class Tick:
    """What one wake-up did."""

    backed_off: bool = False
    gap: tuple[date, date] | None = None
    retried: list[date] = field(default_factory=list)
    billed: list[date] = field(default_factory=list)
    requests: list[UUID] = field(default_factory=list)
    deliveries: int = 0  # event POSTs tried (step 6)
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
                current = billing.get_run_by_id(conn, run["id"])
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
        sent = deliver.process(conn, clock=clock, checkpoint=checkpoint, heartbeat=heartbeat)
        result.deliveries = len(sent.delivered) + len(sent.retrying) + len(sent.failed)
        deliver.purge(conn, clock=clock)
    except Stopped as stopped:
        log.info("%s", stopped)
        result.stopped = True
    return result
