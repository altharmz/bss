"""The worker's clock, and the one test-only override of it (Q14).

The worker reads time only through a Clock. `WORKER_FAKE_NOW` (an ISO 8601 instant with
an offset) pins it to a FixedClock so tests can run the real worker process on a chosen
time - but only when APP_ENV is explicitly "test". Anywhere else (unset - which means
prod -, dev, staging, prod) the worker refuses to start: a pinned clock in a real
environment would bill the wrong dates.
"""

import os
from datetime import datetime

from app.clock import Clock, FixedClock, SystemClock
from app.config import Settings

FAKE_NOW = "WORKER_FAKE_NOW"


class StartRefused(Exception):
    """The worker must not start with this configuration."""


def worker_clock(app_env: str, fake_now: str | None, tz: str | None = None) -> Clock:
    if not fake_now:
        return SystemClock(tz)
    if app_env != "test":
        raise StartRefused(
            f"{FAKE_NOW} is set but APP_ENV is {app_env!r}: the clock override exists for"
            " tests only (APP_ENV=test)"
        )
    try:
        at = datetime.fromisoformat(fake_now)
    except ValueError:
        raise StartRefused(f"{FAKE_NOW}={fake_now!r} is not an ISO 8601 instant") from None
    if at.tzinfo is None:
        raise StartRefused(f"{FAKE_NOW}={fake_now!r} needs an offset (e.g. +03:00)")
    return FixedClock(at, tz)


def clock_from_environment() -> Clock:
    """The clock for this process. Settings are read afresh, so APP_ENV unset means the
    default, prod."""
    return worker_clock(Settings().app_env, os.environ.get(FAKE_NOW))
