"""The worker's side of the UI's requests (worker_request; docs/stage4-design.md section 7).

The internal API never bills: it queues a request, and the worker - which owns billing -
takes them oldest first, marks each picked, carries it out and marks it done:

    catchUp  every date of plan.catch_up_dates, in order, with the catchUp trigger (a run
             that failed as a whole comes first and is not held to the backoff); stops at
             the first whole-run failure, which stays visible in that run
    retry    a run failed because of accounts: retry_run (only its failed accounts, then
             their blocked dates in order); a run failed as a whole: its date again, with
             the retry trigger; a run no longer failed: nothing to do

A request that raises is still marked done (logged), so one bad request cannot block the
queue; its outcome is in the runs. A request picked by a worker that died before finishing
it is picked again.
"""

import logging
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.repository import billing, worker_requests
from app.repository._sql import Row
from app.worker.plan import catch_up_dates
from app.worker.process import HeldBack, NotYet, RunAborted, bill_date, retry_run
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


def process_requests(conn: psycopg.Connection, *, clock: Clock, tz: ZoneInfo) -> list[UUID]:
    """Carry out every pending request, oldest first; the ids done."""
    ensure_ownership(conn)
    done = []
    while True:
        with conn.transaction():
            request = worker_requests.next_pending(conn)
            if request is None:
                return done
            worker_requests.mark_picked(conn, request["id"], clock.now())
        try:
            carry_out(conn, request, clock=clock, tz=tz)
        except Exception:
            log.exception("worker request %s (%s) failed", request["id"], request["kind"])
        with conn.transaction():
            worker_requests.mark_done(conn, request["id"], clock.now())
        done.append(request["id"])


def carry_out(conn: psycopg.Connection, request: Row, *, clock: Clock, tz: ZoneInfo) -> None:
    if request["kind"] == "catchUp":
        _catch_up(conn, request, clock=clock, tz=tz)
    else:
        _retry(conn, request["run_id"], clock=clock, tz=tz)


def _catch_up(conn: psycopg.Connection, request: Row, *, clock: Clock, tz: ZoneInfo) -> None:
    held = billing.held_back_by(conn)
    dates = catch_up_dates(
        billing.latest_run_date(conn), held["run_date"] if held else None, request["up_to"]
    )
    for run_date in dates:
        try:
            bill_date(conn, run_date, clock=clock, tz=tz, trigger="catchUp")
        except (RunAborted, HeldBack, NotYet) as stop:
            log.warning("catch-up to %s stopped at %s: %s", request["up_to"], run_date, stop)
            return


def _retry(conn: psycopg.Connection, run_id: UUID, *, clock: Clock, tz: ZoneInfo) -> None:
    run = billing.get_run_by_id(conn, run_id)
    if run is None or run["status"] != "failed":
        return  # fixed in the meantime (e.g. by a catch-up)
    if run["failure_kind"] == "accounts":
        retry_run(conn, run_id, clock=clock, tz=tz)
        return
    try:
        bill_date(conn, run["run_date"], clock=clock, tz=tz, trigger="retry")
    except (RunAborted, HeldBack) as stop:
        log.warning("retry of %s failed again: %s", run["run_date"], stop)
