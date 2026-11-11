"""The worker's daily housekeeping (#282; docs/decisions/data-model.md "Retention").

From 04:00 to 05:30 BILLING_TZ - after the billing run, the 02:15 backup and the 03:00
reboot - each wake-up of the worker that owns billing:

1. does nothing while a billing run is `running`;
2. creates the usage_record partitions of the current and the next two months (idempotent;
   rows the DEFAULT partition took for such a month move in) and drops the partitions
   older than 13 months (`DROP TABLE`: instant, nothing to vacuum);
3. deletes old rows, table by table (`app/repository/housekeeping.py` RULES), in batches
   of at most 5000 rows, one transaction each, oldest first, 100 ms apart. Each batch logs
   its table and count.

It stops at 05:30 and continues the next day, and gives each wake-up at most TICK_BUDGET,
so the usage feed and the event deliveries of the next wake-ups are not held up by an
hour of deletes. A table with nothing left costs one indexed query per wake-up.
"""

import logging
import time as pause_clock
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.auth import IDLE_SLACK_SECONDS
from app.clock import Clock
from app.config import settings
from app.repository import housekeeping as repo
from app.worker.process import Checkpoint, Heartbeat

log = logging.getLogger("app.worker")

START = time(4, 0)
STOP = time(5, 30)
BATCH = 5000
PAUSE_SECONDS = 0.1
TICK_BUDGET = timedelta(seconds=45)
# partition DDL waits this long for the readers of usage_record, then tries next wake-up
DDL_LOCK_TIMEOUT = "5s"


@dataclass
class Housekeeping:
    """What one wake-up's housekeeping did."""

    skipped: str | None = None  # why it did nothing: "not due" or "billing run running"
    created: list[date] = field(default_factory=list)  # partitions made (their months)
    dropped: list[str] = field(default_factory=list)  # partitions dropped
    deleted: dict[str, int] = field(default_factory=dict)  # table -> rows
    batches: list[tuple[str, int]] = field(default_factory=list)  # (table, rows) each
    finished: bool = False  # every rule ran out of rows


def due(clock: Clock, tz: ZoneInfo) -> bool:
    return START <= clock.now().astimezone(tz).time() < STOP


def housekeep(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
    pause: Callable[[float], None] = pause_clock.sleep,
    budget: timedelta = TICK_BUDGET,
    force: bool = False,
) -> Housekeeping:
    """One wake-up's share. `force` ignores the 04:00-05:30 window (tests, tools); a
    running billing run still stops it."""
    result = Housekeeping()
    if not force and not due(clock, tz):
        result.skipped = "not due"
        return result
    if repo.running_run(conn):
        log.info("housekeeping: a billing run is running; not today's turn yet")
        result.skipped = "billing run running"
        return result
    started = clock.now()
    today = started.astimezone(tz).date()
    _partitions(conn, today, result)

    def stop() -> bool:
        now = clock.now()
        return (
            (checkpoint is not None and checkpoint())
            or now - started >= budget
            or (not force and now.astimezone(tz).time() >= STOP)
        )

    idle = timedelta(seconds=settings.session_idle_seconds + IDLE_SLACK_SECONDS)
    for table, delete in repo.RULES:
        while True:
            if stop():
                return result
            with conn.transaction():
                count = delete(conn, now=clock.now(), today=today, idle=idle, batch=BATCH)
            if count:
                log.info("housekeeping: %s: %d rows deleted", table, count)
                result.deleted[table] = result.deleted.get(table, 0) + count
                result.batches.append((table, count))
            if heartbeat is not None:
                heartbeat()
            if count < BATCH:
                break
            pause(PAUSE_SECONDS)
    result.finished = True
    return result


def _partitions(conn: psycopg.Connection, today: date, result: Housekeeping) -> None:
    """Partitions for the current and the next two months; those older than 13 months go.
    A lock not granted in DDL_LOCK_TIMEOUT is logged and left for the next wake-up."""
    keep_from = repo.month_start(today, -repo.USAGE_MONTHS)
    try:
        for ahead in range(repo.USAGE_AHEAD_MONTHS + 1):
            month = repo.month_start(today, ahead)
            with conn.transaction():
                conn.execute(f"SET LOCAL lock_timeout = '{DDL_LOCK_TIMEOUT}'")
                if repo.create_partition(conn, month):
                    log.info("housekeeping: usage_record partition for %s created", month)
                    result.created.append(month)
        for name, month in sorted(repo.partitions(conn).items(), key=lambda item: item[1]):
            if repo.month_start(month, 1) > keep_from:
                continue
            with conn.transaction():
                conn.execute(f"SET LOCAL lock_timeout = '{DDL_LOCK_TIMEOUT}'")
                repo.drop_partition(conn, name)
            log.info("housekeeping: usage_record partition %s dropped", name)
            result.dropped.append(name)
    except psycopg.errors.LockNotAvailable:
        log.warning("housekeeping: usage_record is busy; partitions are left for the next wake-up")
