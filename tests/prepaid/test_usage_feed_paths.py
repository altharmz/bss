"""The usage feed pass's failure and liveness paths (app/worker/usage.py, #511), against the real
test database and the network stub: an unreachable feed ends the pass with nothing stored, and
each stored batch gets one heartbeat."""
# ruff: noqa: F811

import logging
from datetime import timedelta

import pytest

from app.clock import FixedClock
from app.worker import usage as usage_feed
from app.worker.usage import process_usage
from tests import timeline
from tests.builders.prepaid.usage import MSISDN, line_on
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    state,
    stub,
    world,
)
from tests.builders.worker.logs import worker_lines

pytestmark = pytest.mark.capability("worker.usageFeed")


def backlog(stub, api, count):
    noon = timeline.midnight(api.clock.today()) + timedelta(hours=12)
    for index in range(count):
        stub.inject_usage(f"bulk-{index}", "sms", 1, noon, msisdn=MSISDN)


def feed_pass(api, db_conn, **kwargs):
    created = process_usage(db_conn, clock=FixedClock(api.clock.now()), tz=timeline.TZ, **kwargs)
    db_conn.commit()
    return created


def stored(db_conn):
    count = db_conn.execute("SELECT count(*) FROM usage_record").fetchone()[0]
    db_conn.commit()
    return count


def test_an_unreachable_feed_ends_the_pass_and_the_next_one_stores_the_records(
    api, db_conn, world, stub, monkeypatch, caplog
):
    line_on(api, db_conn, world.off, world.acc)
    backlog(stub, api, 3)
    with monkeypatch.context() as down:
        down.setattr(stub, "usage_records", lambda limit: (_ for _ in ()).throw(OSError("down")))
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            assert feed_pass(api, db_conn) == 0
    assert worker_lines(caplog) == ["usage feed"]
    assert stored(db_conn) == 0 and len(stub.usage_queue) == 3
    assert feed_pass(api, db_conn) == 3
    assert stored(db_conn) == 3 and stub.usage_queue == []


def test_each_stored_batch_gets_one_heartbeat(api, db_conn, world, stub, monkeypatch):
    line_on(api, db_conn, world.off, world.acc)
    monkeypatch.setattr(usage_feed, "BATCH", 5)
    backlog(stub, api, 12)
    beats = []
    assert feed_pass(api, db_conn, heartbeat=lambda: beats.append(1)) == 12
    assert len(beats) == 3  # 5, 5, then the short batch of 2
    assert stored(db_conn) == 12
