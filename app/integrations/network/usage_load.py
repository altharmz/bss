"""Load-test usage for the stub network (#281, docs/loadtest.md "The usage feed").

`BSS_STUB_USAGE_RATE=<records/s>` makes the stub network produce usage records on its own,
at that daily average with a daily shape: `SHAPE` has one factor per local hour of
BILLING_TZ (mean 1; peak 3.04 at 20:00, 0.35 at 04:00, so 23/s gives about 70/s at 20:00
and 8/s at 04:00), linear between the hours. Each record names an MSISDN assigned on the
day, drawn from the database. The records wait in the stub's queue until the worker's
existing feed step stores and acknowledges them; `usage_records` logs the age of the oldest
unacknowledged record (the feed lag).

Refused unless APP_ENV=test: the worker does not start (app/worker/__main__.py) and no
generator is made (`UsageLoad`).
"""

import logging
import random
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.integrations.network.interface import UsageRecord

log = logging.getLogger("app.network.usage_load")

SHAPE = (
    0.80, 0.55, 0.45, 0.40, 0.35, 0.40, 0.50, 0.55, 0.65, 0.75, 0.80, 0.85,
    0.90, 0.90, 0.90, 0.95, 1.05, 1.25, 1.60, 2.20, 3.04, 1.90, 1.30, 0.96,
)  # fmt: skip
_MEAN = sum(SHAPE) / len(SHAPE)
KINDS = (("data", 60), ("voice", 25), ("sms", 15))
REFRESH = timedelta(minutes=10)
# a stalled feed (a billing run) leaves at most this many records waiting; the rest are
# dropped and logged, so a stuck worker cannot fill the stub's memory
MAX_QUEUED = 1_000_000


class LoadRefused(Exception):
    """The usage generator must not run here."""


def check_allowed(app_env: str, rate: float) -> None:
    if rate and app_env != "test":
        raise LoadRefused(
            f"BSS_STUB_USAGE_RATE is set but APP_ENV is {app_env!r}: the generated usage"
            " exists for load tests only (APP_ENV=test)"
        )


def factor(local: datetime) -> float:
    """The shape at a local time: linear between the hourly factors, mean 1 over a day."""
    hour = local.hour + local.minute / 60 + local.second / 3600 + local.microsecond / 3.6e9
    low = int(hour) % 24
    high = (low + 1) % 24
    part = hour - int(hour)
    return (SHAPE[low] * (1 - part) + SHAPE[high] * part) / _MEAN


def expected(rate: float, start: datetime, end: datetime, tz: ZoneInfo) -> float:
    """Records due in [start, end): the integral of the shaped rate. Exact for zones with
    whole-hour offsets (pieces end on whole hours, where the shape's knots are)."""
    total = 0.0
    at = start
    while at < end:
        nxt = min(end, at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        seconds = (nxt - at).total_seconds()
        total += seconds * (factor(at.astimezone(tz)) + factor(nxt.astimezone(tz))) / 2
        at = nxt
    return total * rate


def assigned_numbers(database_url: str, day: date) -> list[str]:
    """The MSISDNs held by a line on `day` (one short connection of its own)."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT n.value FROM number_assignment a"
            " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " WHERE n.resource_specification_id = 'msisdn'"
            " AND a.cancelled_at IS NULL AND a.valid_for @> %s::date",
            (day,),
        ).fetchall()
    return [row[0] for row in rows]


class UsageLoad:
    def __init__(
        self,
        rate: float,
        *,
        app_env: str,
        clock: Clock,
        tz: ZoneInfo,
        numbers: Callable[[date], list[str]],
        seed: int | None = None,
    ) -> None:
        check_allowed(app_env, rate)
        if rate <= 0:
            raise ValueError("the usage rate must be positive")
        self.rate = rate
        self.clock = clock
        self.tz = tz
        self.numbers = numbers
        self.rng = random.Random(seed)  # noqa: S311 - test data
        self.started = clock.now()
        self.since = self.started
        self.carry = 0.0
        self.made = 0
        self.dropped = 0
        self.prefix = f"lt-gen-{self.started.astimezone(UTC):%Y%m%dT%H%M%S}"
        self._pool: list[str] = []
        self._pool_at: datetime | None = None

    def _pool_for(self, now: datetime) -> list[str]:
        if self._pool_at is None or now - self._pool_at >= REFRESH:
            self._pool = self.numbers(now.astimezone(self.tz).date())
            self._pool_at = now
        return self._pool

    def due(self, queued: int) -> list[UsageRecord]:
        """The records produced since the last call, their event times spread evenly over
        the interval; `queued` is how many still wait in the stub."""
        now = self.clock.now()
        if now <= self.since:
            return []
        count_f = expected(self.rate, self.since, now, self.tz) + self.carry
        count = int(count_f)
        self.carry = count_f - count
        start, span = self.since, now - self.since
        self.since = now
        if count == 0:
            return []
        pool = self._pool_for(now)
        if not pool:
            log.warning("stub usage load: no assigned MSISDN, %d records not made", count)
            return []
        room = max(0, MAX_QUEUED - queued)
        if count > room:
            self.dropped += count - room
            log.warning("stub usage load: %d records dropped (queue full)", count - room)
        out = []
        for i in range(min(count, room)):
            kind = self.rng.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
            quantity = {
                "data": self.rng.randint(100_000, 50_000_000),
                "voice": self.rng.randint(10, 600),
                "sms": 1,
            }[kind]
            out.append(
                UsageRecord(
                    id=f"{self.prefix}-{self.made}",
                    kind=kind,
                    quantity=quantity,
                    event_at=start + span * ((i + 1) / count),
                    msisdn=self.rng.choice(pool),
                )
            )
            self.made += 1
        return out
