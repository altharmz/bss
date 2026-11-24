"""Builders other test modules share, from tests/prepaid/test_prepaid_dispatch.py (#423)."""

from app.clock import FixedClock
from app.worker import prepaid
from tests.builders.prepaid.prepaid_posting import TZ
from tests.factories import NOW


def dispatch(conn, now=NOW, **kwargs):
    return prepaid.process_commands(conn, clock=FixedClock(now), tz=TZ, **kwargs)
