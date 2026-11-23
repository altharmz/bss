"""Shared builders of tests/test_worker_schedule.py, used by other test modules (#423)."""

from datetime import date, datetime, time

from tests.timeline import TZ

RUN_AT = time(0, 30)


def msk(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=TZ)
