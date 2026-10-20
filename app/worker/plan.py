"""Pure planning decisions of the worker - no clock, no database.

- When a run that failed as a whole may be tried again (docs/archive/stage4-design.md section 3):
  a whole-run failure (failure_kind 'run': the database gone, a lock timeout, a bug
  outside bill_account) is retried on the same date, never skipped: 5 minutes after the
  first failed attempt, 15 after the second, then hourly.
- Which dates a catch-up to a date runs (section 7): the run that failed as a whole
  first, then every date after the latest run, in order.
- Which dates a scheduled wake-up runs on its own (sections 4 and 6; Q4, Q5): see
  scheduled().
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.repository._sql import Row

BACKOFF = (timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))


def retry_time(run: Row) -> datetime:
    """The earliest moment a run that failed as a whole may run again."""
    if run["status"] != "failed" or run["failure_kind"] != "run":
        raise ValueError(f"billing run {run['run_date']} did not fail as a whole")
    return run["finished_at"] + BACKOFF[min(run["attempt"], len(BACKOFF)) - 1]


def catch_up_dates(latest: date | None, held: date | None, up_to: date) -> list[date]:
    """The dates a catch-up to `up_to` runs, in order: the date of a run that failed as a
    whole (`held`, it comes first - an operator's catch-up is not held to the backoff),
    then every date after the latest existing run up to `up_to`. No run at all: just
    `up_to` (the first run catches up the periods before it, D-W15). A run that failed
    because of accounts is not rerun here - its accounts come back through a retry."""
    if latest is None:
        return [up_to]
    dates = [held] if held is not None and held <= up_to else []
    day = latest + timedelta(days=1)
    while day <= up_to:
        dates.append(day)
        day += timedelta(days=1)
    return dates


def last_due(local_now: datetime, run_at: time) -> date:
    """The latest date whose run is due at `local_now` (a time in BILLING_TZ): today from
    BILLING_RUN_AT on, yesterday before it (Q5 - not 00:00: the margin covers orders that
    completed before midnight but committed after it)."""
    today = local_now.date()
    return today if local_now.time() >= run_at else today - timedelta(days=1)


@dataclass(frozen=True)
class Schedule:
    """What a wake-up bills on its own: `dates`, in order; `gap` = the first and last
    missed date when dates older than the automatic window are missing (then nothing is
    billed - an operator catches up; the gap is reported, never billed silently)."""

    dates: tuple[date, ...]
    gap: tuple[date, date] | None = None


def scheduled(
    latest: date | None,
    held: date | None,
    local_now: datetime,
    run_at: time,
    unfinished: date | None = None,
) -> Schedule:
    """The dates a wake-up runs without anyone asking (Q4):

    - a run that failed as a whole: only its date (no later date before it succeeds; the
      backoff is the caller's - bill_date raises NotYet);
    - a run left running (its worker was killed or stopped mid-run - the 03:00 reboot, a
      deploy): its date first, taken over by the new owner; then the dates below;
    - no run at all: the due date only (the first run catches up earlier periods, D-W15);
    - otherwise every date after the latest run up to the due date, but only if they all
      lie in the automatic window - the due date and the day before it. An older missing
      date is a gap: nothing is billed (billing a later date past it would leave it
      impossible to replay in order), the gap is reported.
    """
    due = last_due(local_now, run_at)
    if held is not None:
        return Schedule((held,))
    if latest is None:
        return Schedule((due,))
    resume = (unfinished,) if unfinished is not None else ()
    first = latest + timedelta(days=1)
    if first > due:
        return Schedule(resume)
    if first < due - timedelta(days=1):
        return Schedule(resume, gap=(first, due - timedelta(days=2)))
    dates = tuple(first + timedelta(days=i) for i in range((due - first).days + 1))
    return Schedule(resume + dates)
