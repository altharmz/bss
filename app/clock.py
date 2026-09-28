"""The only place in app/ allowed to read the wall clock (enforced by tests/test_clock.py).

Everything time-dependent receives a Clock, or a `now`/`today` value derived from one, so
tests can pin time with FixedClock.
"""

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import settings


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, timezone-aware."""
        ...

    def today(self) -> date:
        """Current calendar date in the billing timezone."""
        ...


class SystemClock:
    def __init__(self, tz: str | None = None) -> None:
        self.tz = ZoneInfo(tz or settings.billing_tz)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().astimezone(self.tz).date()


class FixedClock:
    """A clock that stands still (or moves only when told to). For tests."""

    def __init__(self, at: datetime, tz: str | None = None) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self.at = at
        self.tz = ZoneInfo(tz or settings.billing_tz)

    def now(self) -> datetime:
        return self.at

    def today(self) -> date:
        return self.at.astimezone(self.tz).date()

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self.at = at
