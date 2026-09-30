"""One clock for the API tests. The `api` fixture's FixedClock starts at NOW, and every
date-time the API tests send or expect is derived from it - never written as a literal."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
TZ = ZoneInfo(settings.billing_tz)
TODAY = NOW.astimezone(TZ).date()  # "today" for billing
TOMORROW = TODAY + timedelta(days=1)


def midnight(day: date) -> datetime:
    """Start of `day` in BILLING_TZ."""
    return datetime.combine(day, time.min, tzinfo=TZ)


def iso(value: datetime) -> str:
    """As a client would send it, with its offset."""
    return value.isoformat()


def z(value: datetime) -> str:
    """As the API returns it: UTC with a Z."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# Prices cannot start in the past, and a product priced on a day needs a price valid from
# that day's midnight: the earliest usable price start is the next midnight after NOW.
START = midnight(TOMORROW)
