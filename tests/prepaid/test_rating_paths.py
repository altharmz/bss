"""The rating pass's stop, race and failure paths (app/worker/rating.py, #511), against the real
test database and the network stub: checkpoints and heartbeats in the rating, queueing and
sending loops, a barring settled before it was sent, a line that ended before its barring went
out, and a send whose outcome is unknown."""
# ruff: noqa: F811

import logging
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.clock import FixedClock
from app.integrations.network import adapter as network
from app.repository import usage_bars
from app.worker import rating
from app.worker.rating import process_rating, queue_bars, send_bars
from tests import apidata
from tests.builders.prepaid.payg_rating import payg, report  # noqa: F401 - fixtures
from tests.builders.prepaid.prepaid_plans import at, balance, build, stubs  # noqa: F401
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.worker.logs import worker_lines
from tests.timeline import TZ

pytestmark = pytest.mark.capability("worker.usageRating")

MIB = 1024 * 1024


def clock(api):
    return FixedClock(api.clock.now())


def rating_pass(api, db_conn, **kwargs):
    rated = process_rating(db_conn, clock=clock(api), tz=TZ, **kwargs)
    db_conn.commit()
    return rated


def tomorrow(api):
    api.clock.set(at(api.clock.today() + timedelta(days=1)))


def bar_rows(db_conn):
    rows = db_conn.execute(
        "SELECT kind, state, attempts, last_error FROM usage_bar ORDER BY created_at"
    ).fetchall()
    db_conn.commit()
    return rows


def sent(kind):
    return [key for key, (command, _) in network().received.items() if command == kind]


def overspent(api, db_conn, payg):
    """A pay-as-you-go line whose closed day is rated below zero; its suspension not queued."""
    world = payg("1")
    report(api, world.product, "data", 10 * MIB, api.clock.now())  # 1.00 + 0.20 tax
    tomorrow(api)
    answers = iter([False])  # rate the account, then stop before the barring
    assert rating_pass(api, db_conn, checkpoint=lambda: next(answers, True)) == 1
    assert balance(db_conn, world.acc) == D("-0.20") and bar_rows(db_conn) == []
    return world


def test_a_checkpoint_stops_rating_before_the_next_account_and_each_account_gets_a_heartbeat(
    api, db_conn, payg
):
    world = payg("10")
    record = report(api, world.product, "data", 2 * MIB, api.clock.now())
    tomorrow(api)
    assert rating_pass(api, db_conn, checkpoint=lambda: True) == 0
    assert api.get(record["href"]).json()["status"] != "rated"
    assert balance(db_conn, world.acc) == D("10")
    beats = []
    assert rating_pass(api, db_conn, heartbeat=lambda: beats.append(1)) == 1
    assert api.get(record["href"]).json()["status"] == "rated"
    assert beats == [1]  # one account; no barring to send


def test_a_checkpoint_stops_queueing_and_sending_and_each_send_gets_a_heartbeat(api, db_conn, payg):
    overspent(api, db_conn, payg)
    assert queue_bars(db_conn, clock=clock(api), tz=TZ, checkpoint=lambda: True) == []
    db_conn.commit()
    assert bar_rows(db_conn) == []
    [queued] = queue_bars(db_conn, clock=clock(api), tz=TZ)
    db_conn.commit()
    assert send_bars(db_conn, clock=clock(api), tz=TZ, checkpoint=lambda: True) == []
    db_conn.commit()
    assert bar_rows(db_conn) == [("suspend", "pending", 0, None)] and sent("suspend") == []
    beats = []
    assert send_bars(db_conn, clock=clock(api), tz=TZ, heartbeat=lambda: beats.append(1)) == [
        queued
    ]
    db_conn.commit()
    assert beats == [1] and len(sent("suspend")) == 1
    assert bar_rows(db_conn) == [("suspend", "confirmed", 1, None)]


def test_a_barring_settled_after_it_was_listed_is_not_sent(api, db_conn, payg, monkeypatch):
    world = payg("10")
    bar = usage_bars.create(db_conn, UUID(world.product["id"]), "suspend", api.clock.now())
    listed = usage_bars.due(db_conn, api.clock.now())
    usage_bars.cancel(db_conn, bar["id"])  # settled between the listing and the lock
    db_conn.commit()
    with monkeypatch.context() as patch:
        patch.setattr(rating.bars, "due", lambda conn, now: listed)
        assert send_bars(db_conn, clock=clock(api), tz=TZ) == []
    db_conn.commit()
    assert bar_rows(db_conn) == [("suspend", "cancelled", 0, None)] and sent("suspend") == []


def test_a_barring_of_a_line_that_ended_before_it_was_sent_is_cancelled(api, db_conn, payg):
    world = payg("10")
    placed = apidata.order(api, world.acc, apidata.delete_item(world.product["id"]))
    for minutes in (1, 2):
        tick(db_conn, api.clock.now() + timedelta(minutes=minutes))
    assert api.get(placed["href"]).json()["state"] == "completed"
    usage_bars.create(db_conn, UUID(world.product["id"]), "suspend", api.clock.now())
    db_conn.commit()
    tomorrow(api)
    assert send_bars(db_conn, clock=clock(api), tz=TZ) == []
    db_conn.commit()
    assert bar_rows(db_conn) == [("suspend", "cancelled", 0, None)] and sent("suspend") == []


def test_a_send_with_an_unknown_outcome_is_sent_again_with_the_same_key(
    api, db_conn, payg, monkeypatch, caplog
):
    overspent(api, db_conn, payg)
    [queued] = queue_bars(db_conn, clock=clock(api), tz=TZ)
    db_conn.commit()
    original = network().send

    def lost(key, command, request):
        raise RuntimeError()

    network().send = lost
    try:
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            assert send_bars(db_conn, clock=clock(api), tz=TZ) == [queued]
        db_conn.commit()
    finally:
        del network().send
    assert worker_lines(caplog) == [f"usage bar {queued}"]
    assert bar_rows(db_conn) == [("suspend", "pending", 1, "RuntimeError")]
    assert original is not None
    api.clock.set(api.clock.now() + timedelta(hours=1))
    assert send_bars(db_conn, clock=clock(api), tz=TZ) == [queued]
    db_conn.commit()
    assert bar_rows(db_conn) == [("suspend", "confirmed", 2, None)]
    assert sent("suspend") == [str(queued)]
