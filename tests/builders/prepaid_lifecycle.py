"""Shared builders of tests/test_prepaid_lifecycle.py, used by other test modules (#423)."""

from datetime import datetime, time, timedelta
from uuid import UUID

import pytest

from tests import timeline
from tests.builders.network_provisioning import buy as buy_line
from tests.builders.network_provisioning import make_line
from tests.builders.prepaid_orders import shop
from tests.builders.prepaid_renewal_producer import pay, tick


def at(day, hour=9):
    return datetime.combine(day, time(hour), timeline.TZ)


def go(api, db_conn, day, ticks=3):
    """The business clock is `day`; the worker wakes up a few times (commands are sent on
    one tick and posted on the next)."""
    api.clock.set(at(day))
    for _ in range(ticks):
        tick(db_conn, api.clock.now())


def top_up(api, db_conn, acc, amount, day, ticks=3):
    """A payment received after the worker last looked at the account, then a few wake-ups."""
    pay(db_conn, UUID(acc["id"]), amount, at(day) + timedelta(minutes=1))
    api.clock.set(at(day) + timedelta(minutes=2))
    for _ in range(ticks):
        tick(db_conn, at(day) + timedelta(minutes=2))


@pytest.fixture
def numbered(api, db_conn, stub):
    """A prepaid line with a number, paid for one month (12) and nothing left."""
    off, acc = shop(api, db_conn, funds="12", amount=10, numbers=True)
    make_line(api, off)
    order = buy_line(api, SimpleNamespaceWorld(off, acc), "12125550177")
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "completed"
    product_id, due = db_conn.execute(
        "SELECT f.product_id, upper(f.period) FROM funded_interval f"
    ).fetchone()
    stub.received.clear()
    return SimpleNamespaceWorld(off, acc, product=product_id, due=due, order=order)


class SimpleNamespaceWorld:
    def __init__(self, off, acc, **rest):
        self.off, self.acc = off, acc
        self.__dict__.update(rest)
