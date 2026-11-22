"""Shared builders of tests/test_prepaid_dispatch.py, used by other test modules (#423)."""

from app.clock import FixedClock
from app.worker import prepaid
from tests.builders.prepaid_posting import TZ
from tests.factories import NOW


def dispatch(conn, now=NOW, **kwargs):
    return prepaid.process_commands(conn, clock=FixedClock(now), tz=TZ, **kwargs)
