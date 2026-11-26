"""The port worker's failure, stop and race paths (#483), against the real test database.

The network stub is the carrier; a test scripts a failure on it where the path needs one, so
every path ends in a checked port request state, the carrier calls made and the line."""
# ruff: noqa: F811

import logging
from datetime import timedelta

import pytest

from app.clock import FixedClock
from app.config import settings
from app.integrations.network import NetworkRefused
from app.repository import ports as port_rows
from app.worker import ports
from tests.builders.orders.api_suspension import change
from tests.builders.prepaid.prepaid_posting import TZ
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    line,
    stub,
    world,
)
from tests.builders.resources.number_porting import PORT, port_in

PIN = "/internal/v1/port/pin"
LINE = "12125550199"


def run(db_conn, api, minutes=0, **kwargs):
    now = api.clock.now() + timedelta(minutes=minutes)
    ports.process_ports(db_conn, clock=FixedClock(now), tz=TZ, **kwargs)
    db_conn.commit()


def port_of(api, port):
    return api.get(f"{PORT}/{port['id']}").json()


def outs(api):
    return {p["carrierReference"]: p for p in api.get(f"{PORT}?direction=out").json()}


def answers(values):
    """A checkpoint that answers `values` in turn, then never stops."""
    values = list(values)
    return lambda: values.pop(0) if values else False


def pin_for(api, product_id):
    response = api.post(PIN, {"productId": product_id})
    assert response.status_code == 201, response.text
    return response.json()["pin"]


def product_row(db_conn, product_id):
    row = db_conn.execute(
        "SELECT status, termination_date FROM product WHERE id = %s", (product_id,)
    ).fetchone()
    db_conn.commit()
    return row


def test_an_unreachable_carrier_feed_is_logged_and_the_port_ins_still_go(
    api, db_conn, stub, monkeypatch, caplog
):
    stub.inject_port_out("OUT-1", "19998887766", "123456")
    port = port_in(api)

    real, down = stub.port_out_requests, [True]

    def feed():
        if down:
            raise ConnectionError("carrier feed down")
        return real()

    monkeypatch.setattr(stub, "port_out_requests", feed)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        run(db_conn, api)
    assert "port-out requests" in caplog.text
    assert outs(api) == {}  # nothing recorded while the feed is down
    assert port_of(api, port)["state"] == "pending"  # the port-in was submitted regardless
    down.clear()
    run(db_conn, api, minutes=1)
    assert outs(api)["OUT-1"]["rejectionCode"] == "numberUnknown"


def test_a_checkpoint_stops_each_phase_and_each_request_gets_one_heartbeat(api, db_conn, stub):
    stub.inject_port_out("OUT-1", "19998887766", "123456")
    port = port_in(api)
    beats = []
    beat = lambda: beats.append(1)  # noqa: E731

    run(db_conn, api, checkpoint=answers([True]), heartbeat=beat)  # before the port-out
    assert outs(api) == {} and stub.port_in == {} and beats == []

    # the port-out is decided and answered, then the stop comes before the port-in is sent
    run(db_conn, api, checkpoint=answers([False, True]), heartbeat=beat)
    assert outs(api)["OUT-1"]["answeredAt"] is not None and beats == [1]
    assert port_of(api, port)["state"] == "submitted" and stub.port_in == {}

    # sent: the stop now comes before the port-in is read, which waits for the next tick
    run(db_conn, api, minutes=1, heartbeat=beat)
    assert port_of(api, port)["state"] == "pending" and beats == [1, 1]
    run(db_conn, api, minutes=2, checkpoint=answers([True]), heartbeat=beat)
    assert port_of(api, port)["state"] == "pending" and beats == [1, 1]
    run(db_conn, api, minutes=3, heartbeat=beat)
    assert port_of(api, port)["state"] == "completed" and beats == [1, 1, 1]
    assert len(stub.port_in) == 1  # submitted once, read until it completed


def test_a_port_in_the_carrier_refuses_at_submission_is_rejected_with_its_reason(
    api, db_conn, stub, monkeypatch
):
    port = port_in(api)

    def refuse(key, request):
        stub.attempts.append(key)
        raise NetworkRefused("the number is not portable", "numberNotPortable")

    monkeypatch.setattr(stub, "submit_port_in", refuse)
    run(db_conn, api)
    rejected = port_of(api, port)
    assert (rejected["state"], rejected["rejectionCode"], rejected["rejectionReason"]) == (
        "rejected",
        "numberNotPortable",
        "the number is not portable",
    )
    run(db_conn, api, minutes=60)
    assert len(stub.attempts) == 1  # a refusal is final: never sent again


def test_an_unexpected_submission_error_is_logged_retried_then_rejected(
    api, db_conn, stub, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "network_max_attempts", 2)
    port = port_in(api)
    errors = [RuntimeError("socket closed"), KeyError()]

    def broken(key, request):
        stub.attempts.append(key)
        raise errors.pop(0)

    monkeypatch.setattr(stub, "submit_port_in", broken)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        run(db_conn, api)
    assert f"port-in {port['id']}" in caplog.text  # an unknown error is logged, unlike a temporary
    first = port_of(api, port)
    assert (first["state"], first["attempts"]) == ("submitted", 1)
    run(db_conn, api, minutes=1)  # the backoff has passed: the last attempt
    rejected = port_of(api, port)
    assert (rejected["state"], rejected["rejectionCode"]) == ("rejected", "networkUnavailable")
    # an error without a message is named by its type
    assert rejected["rejectionReason"] == "the carrier could not be reached: KeyError"
    assert stub.attempts[0] == stub.attempts[1]  # one submission key for both attempts


def test_a_failed_status_read_is_logged_and_read_again(api, db_conn, stub, monkeypatch, caplog):
    port = port_in(api)
    run(db_conn, api)
    assert port_of(api, port)["state"] == "pending"
    real = stub.port_in_status
    reads = []

    def flaky(reference):
        reads.append(reference)
        if len(reads) == 1:
            raise TimeoutError("read timed out")
        return real(reference)

    monkeypatch.setattr(stub, "port_in_status", flaky)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        run(db_conn, api, minutes=1)
    assert f"port-in status {port['id']}" in caplog.text
    assert port_of(api, port)["state"] == "pending"
    run(db_conn, api, minutes=2)
    assert port_of(api, port)["state"] == "completed"
    assert reads[0] == reads[1] == port_of(api, port)["carrierReference"]


def test_a_port_in_cancelled_after_the_scan_is_neither_sent_nor_read(
    api, db_conn, stub, monkeypatch
):
    port = port_in(api)
    to_submit, to_poll = port_rows.to_submit, port_rows.to_poll
    racing = {"submit": port["id"]}

    def raced_submit(conn, now):
        rows = to_submit(conn, now)
        if racing.get("submit"):
            conn.execute(
                "UPDATE port_request SET state = 'cancelled' WHERE id = %s", (racing.pop("submit"),)
            )
        return rows

    def raced_poll(conn):
        rows = to_poll(conn)
        if racing.get("poll"):
            conn.execute(
                "UPDATE port_request SET state = 'cancelled' WHERE id = %s", (racing.pop("poll"),)
            )
        return rows

    monkeypatch.setattr(port_rows, "to_submit", raced_submit)
    monkeypatch.setattr(port_rows, "to_poll", raced_poll)
    run(db_conn, api)
    assert stub.attempts == [] and port_of(api, port)["state"] == "cancelled"

    other = port_in(api)  # the cancelled one freed the number
    run(db_conn, api, minutes=1)  # sent: pending
    assert port_of(api, other)["state"] == "pending"
    racing["poll"] = other["id"]
    monkeypatch.setattr(stub, "port_in_status", lambda ref: pytest.fail("read a cancelled port"))
    run(db_conn, api, minutes=2)
    assert port_of(api, other)["state"] == "cancelled"


def test_a_used_pin_on_a_line_that_is_still_active_is_refused(api, db_conn, world, stub):
    product = line(api, db_conn, world, LINE)
    pin = pin_for(api, product["id"])
    db_conn.execute("UPDATE number_transfer_pin SET used_at = %s", (api.clock.now(),))
    db_conn.commit()
    stub.inject_port_out("OUT-1", LINE, pin)
    run(db_conn, api)
    refused = outs(api)["OUT-1"]
    assert (refused["rejectionCode"], refused["rejectionReason"]) == (
        "pinInvalid",
        "the Number Transfer PIN was already used",
    )
    assert product_row(db_conn, product["id"]) == ("active", None)


def test_a_port_out_whose_termination_order_is_refused_changes_nothing(api, db_conn, world, stub):
    product = line(api, db_conn, world, LINE)
    pin = pin_for(api, product["id"])
    later = api.clock.now().astimezone(TZ).date() + timedelta(days=10)
    # an order suspends the line in ten days: an order ending it now is refused until then
    change(api, world.acc, product, "suspended", later, on=api.clock.now())
    stub.inject_port_out("OUT-1", LINE, pin)
    run(db_conn, api)
    refused = outs(api)["OUT-1"]
    assert refused["state"] == "rejected" and refused["rejectionCode"] == "terminationRefused"
    assert "changes its status on" in refused["rejectionReason"]
    assert refused["productOrder"] is None
    assert stub.port_answers[next(iter(stub.port_answers))][1:] == (
        False,
        refused["rejectionReason"],
    )
    # the whole decision rolled back: the line, its PIN and its number are untouched
    assert product_row(db_conn, product["id"]) == ("active", None)
    used = db_conn.execute("SELECT used_at FROM number_transfer_pin").fetchone()
    deletes = db_conn.execute(
        "SELECT count(*) FROM product_order_item WHERE action = 'delete'"
    ).fetchone()
    db_conn.commit()
    assert used == (None,) and deletes == (0,)
    resource = api.get(f"/tmf-api/resourceInventory/v5/resource?value={LINE}").json()[0]
    assert resource["administrativeState"] == "unlocked"
