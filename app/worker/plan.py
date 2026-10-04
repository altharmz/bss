"""Pure planning decisions of the worker - no clock, no database.

- When a run that failed as a whole may be tried again (docs/stage4-design.md section 3):
  a whole-run failure (failure_kind 'run': the database gone, a lock timeout, a bug
  outside bill_account) is retried on the same date, never skipped: 5 minutes after the
  first failed attempt, 15 after the second, then hourly.
- Which dates a catch-up to a date runs (section 7): the run that failed as a whole
  first, then every date after the latest run, in order.
"""

from datetime import date, datetime, timedelta

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
