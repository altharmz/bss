"""The network worker's failure, stop, race and replay paths (#483), against the real test
database.

The network stub answers the commands; a test scripts a failure on it where the path needs
one, so every path ends in a checked command state, the requests the network got and the
order or prepaid command the operator sees."""
# ruff: noqa: F811

import logging
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.clock import FixedClock
from app.integrations.network import NetworkTemporary
from app.repository import network_commands, products
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from app.worker import network, prepaid
from tests.apidata import ORDER
from tests.builders.prepaid.prepaid_offering_changes import at, switch
from tests.builders.prepaid.prepaid_posting import TZ
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    buy,
    commands_of,
    line,
    make_line,
    state,
    stub,
    world,
)

POSTS = (
    (network.post_profile_change, "profile_changes", "profile command"),
    (network.post_sim_swap, "sim_swaps", "sim swap command"),
    (network.post_e911_change, "e911_changes", "E911 command"),
)


def send(db_conn, api, minutes=0, **kwargs):
    sent = network.process_network(
        db_conn, clock=FixedClock(api.clock.now() + timedelta(minutes=minutes)), tz=TZ, **kwargs
    )
    db_conn.commit()
    return sent


def command_of(db_conn, order):
    row = db_conn.execute(
        "SELECT id, state, attempts, last_error FROM network_command WHERE product_order_id = %s",
        (order["id"],),
    ).fetchone()
    db_conn.commit()
    return row


def test_a_checkpoint_stops_before_the_next_command_and_each_send_gets_one_heartbeat(
    api, db_conn, world, stub
):
    order = buy(api, world, "79991234500")
    beats = []
    assert send(db_conn, api, checkpoint=lambda: True, heartbeat=lambda: beats.append(1)) == []
    assert stub.attempts == [] and beats == []
    assert commands_of(db_conn, order) == [("activate", "pending", 0)]
    (cid,) = send(db_conn, api, heartbeat=lambda: beats.append(1))
    assert beats == [1] and stub.attempts == [str(cid)]
    assert commands_of(db_conn, order) == [("activate", "confirmed", 1)]


def test_a_command_another_worker_handled_after_the_scan_is_not_sent(
    api, db_conn, world, stub, monkeypatch
):
    order = buy(api, world, "79991234500")
    due = network_commands.due

    def raced(conn, today, now):
        rows = due(conn, today, now)
        conn.execute(
            "UPDATE network_command SET state = 'cancelled' WHERE product_order_id = %s",
            (order["id"],),
        )
        return rows

    monkeypatch.setattr(network_commands, "due", raced)
    assert send(db_conn, api) == []
    assert stub.attempts == []  # no longer pending under the lock: not sent twice


def test_activation_without_its_product_lookup_sends_no_pool(
    api, db_conn, world, stub, monkeypatch
):
    make_line(api, world.off, dataPool=1)
    order = buy(api, world, "79991234500")
    cmd = network_commands.of_order(db_conn, UUID(order["id"]))[0]
    real = products.get_product

    def missing(conn, product_id):
        return None if product_id == cmd["product_id"] else real(conn, product_id)

    with monkeypatch.context() as patch:
        patch.setattr(products, "get_product", missing)
        assert send(db_conn, api) == [cmd["id"]]
    kind, request = stub.received[str(cmd["id"])]
    assert (kind, request["msisdn"]) == ("activate", "79991234500")
    assert "dataPoolId" not in request and "dataPoolMb" not in request
    stored = network_commands.get(db_conn, cmd["id"])
    assert (stored["state"], stored["data_pool"], stored["request"]) == (
        "confirmed",
        None,
        request,
    )


def test_a_renewal_waits_for_its_due_profile_change(api, db_conn, world, stub, monkeypatch):
    product = line(api, db_conn, world)
    order = switch(api, world.acc, product, world.cheaper, product["due"])
    assert order["state"] == "inProgress"
    now = at(product["due"], 0)
    renewal = prepaid.renewal_id(product["grant"])
    real = stub.send

    def unavailable(key, kind, request):
        if kind == "change_profile":
            raise NetworkTemporary("profile unavailable")
        return real(key, kind, request)

    assert prepaid.produce_renewals(db_conn, clock=FixedClock(now), tz=TZ) == [renewal]
    with monkeypatch.context() as patch:
        patch.setattr(stub, "send", unavailable)
        assert prepaid.process_commands(db_conn, clock=FixedClock(now), tz=TZ) == []
    db_conn.commit()
    waiting = commands.get(db_conn, renewal)
    assert (waiting["state"], waiting["attempts"]) == ("pending", 0)
    assert state(api, order) == "inProgress"

    prepaid.process_commands(db_conn, clock=FixedClock(now + timedelta(hours=1)), tz=TZ)
    db_conn.commit()
    assert state(api, order) == "completed"
    assert commands.get(db_conn, renewal)["state"] == "completed"


def test_an_unexpected_send_error_is_logged_and_sent_again_with_the_same_key(
    api, db_conn, world, stub, monkeypatch, caplog
):
    order = buy(api, world, "79991234500")
    real = stub.send
    errors = [ConnectionResetError("peer reset"), RuntimeError()]

    def flaky(key, command, request):
        if errors:
            stub.attempts.append(key)
            raise errors.pop(0)
        return real(key, command, request)

    monkeypatch.setattr(stub, "send", flaky)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        send(db_conn, api)
    cid, *first = command_of(db_conn, order)
    assert f"network command {cid}" in caplog.text
    assert first == ["pending", 1, "peer reset"]
    send(db_conn, api, minutes=60)
    # an error without a message is named by its type
    assert command_of(db_conn, order)[1:] == ("pending", 2, "RuntimeError")
    assert stub.received == {}
    send(db_conn, api, minutes=600)
    assert command_of(db_conn, order)[1] == "confirmed"
    assert stub.attempts == [str(cid)] * 3 and len(stub.received) == 1


@pytest.mark.parametrize(("post", "kind", "subject"), POSTS)
def test_a_posting_without_billing_ownership_is_refused(api, db_conn, post, kind, subject):
    worker_db.release_ownership(db_conn)
    assert not worker_db.holds_ownership(db_conn)
    with pytest.raises(ValueError, match="prepaid posting requires billing ownership"):
        post(db_conn, uuid4(), now=api.clock.now(), tz=TZ)


def queued(db_conn, api, world, kind, order_id):
    """A due prepaid command of `kind` for `order_id`, as the order API would queue it."""
    assert worker_db.acquire_ownership(db_conn)
    cid = uuid4()
    commands.enqueue(
        db_conn,
        cid,
        UUID(world.acc["id"]),
        kind="order",
        due_date=api.clock.now().astimezone(TZ).date(),
        request={"order_id": str(order_id), kind: []},
        now=api.clock.now(),
    )
    db_conn.commit()
    return cid


@pytest.mark.parametrize(("post", "kind", "subject"), POSTS)
def test_a_posting_whose_order_is_no_longer_in_progress_is_refused(
    api, db_conn, world, stub, post, kind, subject
):
    line(api, db_conn, world)
    (done,) = db_conn.execute("SELECT id FROM product_order WHERE state = 'completed'").fetchone()
    cid = queued(db_conn, api, world, kind, done)
    with pytest.raises(ValueError, match=f"{subject} must match its pending order"):
        post(db_conn, cid, now=api.clock.now(), tz=TZ)
    db_conn.rollback()
    assert commands.get(db_conn, cid)["state"] == "pending"
    db_conn.commit()


@pytest.mark.parametrize(("post", "kind", "subject"), POSTS)
def test_a_completed_posting_replays_its_result_and_changes_nothing(
    api, db_conn, world, stub, post, kind, subject
):
    line(api, db_conn, world)
    (done,) = db_conn.execute("SELECT id FROM product_order WHERE state = 'completed'").fetchone()
    cid = queued(db_conn, api, world, kind, done)
    stored = {"outcome": "kept"}
    commands.complete(db_conn, cid, stored, now=api.clock.now())
    db_conn.commit()
    events = db_conn.execute("SELECT count(*) FROM event").fetchone()
    assert post(db_conn, cid, now=api.clock.now(), tz=TZ) == stored
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM event").fetchone() == events
    assert state(api, {"href": f"{ORDER}/{done}"}) == "completed"
