"""When a run that failed as a whole may be tried again (docs/stage4-design.md, section 3).

A whole-run failure (failure_kind 'run': the database gone, a lock timeout, a bug outside
bill_account) is retried on the same date, never skipped: 5 minutes after the first
failed attempt, 15 after the second, then hourly. Pure - no clock, no database.
"""

from datetime import datetime, timedelta

from app.repository._sql import Row

BACKOFF = (timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))


def retry_time(run: Row) -> datetime:
    """The earliest moment a run that failed as a whole may run again."""
    if run["status"] != "failed" or run["failure_kind"] != "run":
        raise ValueError(f"billing run {run['run_date']} did not fail as a whole")
    return run["finished_at"] + BACKOFF[min(run["attempt"], len(BACKOFF)) - 1]
