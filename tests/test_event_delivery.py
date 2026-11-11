"""Delivering events: the worker POSTs each due delivery to its hub's callback.

A local listener (an HTTP server on a thread, reachable because tests run with APP_ENV
dev) stands in for a subscriber. 2xx = delivered; anything else is retried after 1 min,
5 min, 30 min, 2 h and 12 h, then failed. Outside dev the callback is checked again
right before the POST and the connection goes to the checked address.
"""

import http.server
import json
import socket
import threading
from datetime import datetime, time, timedelta

import pytest

from app.clock import FixedClock
from app.config import settings
from app.events import callbacks, deliver
from app.events.types import BASES
from app.worker import db as worker_db
from app.worker.housekeeping import housekeep
from tests import apidata
from tests.timeline import TZ


class Listener:
    """Records what it receives; answers `status` (a list: one per request, last repeats)."""

    def __init__(self, statuses=(204,)):
        self.received: list[tuple[dict, bytes]] = []
        self.statuses = list(statuses)
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                outer.received.append((dict(self.headers), body))
                status = outer.statuses.pop(0) if len(outer.statuses) > 1 else outer.statuses[0]
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/events?source=bss"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def listener():
    created = []

    def make(statuses=(204,)):
        server = Listener(statuses)
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


def hub(api, callback: str, name: str = "TMF632") -> dict:
    response = api.post(f"{BASES[name]}/hub", {"callback": callback})
    assert response.status_code == 201, response.text
    return response.json()


def delivery_rows(db_conn):
    return db_conn.execute(
        "SELECT state::text, attempts, next_attempt_at, last_error FROM event_delivery"
        " ORDER BY created_at"
    ).fetchall()


@pytest.mark.capability("worker.eventDelivery")
def test_an_event_reaches_its_callback_exactly_as_stored(api, db_conn, listener):
    server = listener()
    hub(api, server.url)
    person = apidata.individual(api)
    sent = deliver.process(db_conn, clock=api.clock)
    assert len(sent.delivered) == 1
    ((headers, body),) = server.received
    assert headers["Content-Type"] == "application/json"
    stored = db_conn.execute("SELECT payload::text FROM event").fetchone()[0]
    assert json.loads(body) == json.loads(stored)
    event = json.loads(body)
    assert event["eventType"] == "IndividualCreateEvent"
    assert event["event"]["individual"]["id"] == person["id"]
    (row,) = delivery_rows(db_conn)
    assert row[:2] == ("delivered", 1) and row[2] is None
    assert deliver.process(db_conn, clock=api.clock).delivered == []  # nothing due


def test_money_goes_out_as_an_exact_number(api, db_conn, listener):
    server = listener()
    hub(api, server.url, "TMF676")
    acc = apidata.account(api)
    api.post_raw(
        apidata.PAYMENT,
        f'{{"account":{{"id":"{acc["id"]}"}},"totalAmount":{{"unit":"USD","value":19.90}},'
        '"paymentMethod":{"name":"bankTransfer","@type":"BankAccountTransfer"}}',
    )
    deliver.process(db_conn, clock=api.clock)
    ((_, body),) = server.received
    assert b'"value": 19.90' in body


def test_a_failing_callback_is_retried_with_backoff_then_given_up(api, db_conn, listener):
    server = listener(statuses=(500,))
    hub(api, server.url)
    apidata.organization(api)
    waits = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
    waits += [timedelta(hours=2), timedelta(hours=12)]
    for attempt, wait in enumerate(waits, start=1):
        start = api.clock.now()
        assert deliver.process(db_conn, clock=api.clock).retrying
        state, attempts, due, error = delivery_rows(db_conn)[0]
        assert (state, attempts, error) == ("pending", attempt, "HTTP 500 Internal Server Error")
        assert due == start + wait
        assert deliver.process(db_conn, clock=api.clock).retrying == []  # not due yet
        api.clock.set(due)
    assert deliver.process(db_conn, clock=api.clock).failed
    state, attempts, due, _ = delivery_rows(db_conn)[0]
    assert (state, attempts, due) == ("failed", 6, None)
    assert len(server.received) == 6


def test_a_callback_that_recovers_gets_the_event(api, db_conn, listener):
    server = listener(statuses=(503, 200))
    hub(api, server.url)
    apidata.organization(api)
    deliver.process(db_conn, clock=api.clock)
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    assert len(deliver.process(db_conn, clock=api.clock).delivered) == 1
    assert delivery_rows(db_conn)[0][:2] == ("delivered", 2)


def test_an_unreachable_callback_is_an_error_not_a_crash(api, db_conn):
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    hub(api, f"http://127.0.0.1:{port}/x")
    apidata.organization(api)
    assert deliver.process(db_conn, clock=api.clock).retrying
    assert "ConnectionRefusedError" in delivery_rows(db_conn)[0][3]


def test_a_deleted_hub_receives_nothing_more(api, db_conn, listener):
    server = listener()
    subscription = hub(api, server.url)
    apidata.organization(api)
    assert api.delete(subscription["href"]).status_code == 204
    assert deliver.process(db_conn, clock=api.clock).delivered == []
    assert server.received == []
    assert delivery_rows(db_conn)[0][0] == "dropped"


def test_outside_dev_the_address_is_checked_again_before_sending(api, db_conn, monkeypatch):
    """The hub was accepted while its host resolved to a public address; by the time of
    the POST it resolves to the shared Docker network - refused, nothing is sent."""
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(callbacks, "resolve", lambda host, port: ["93.184.216.34"])
    hub(api, "https://listener.example/events")
    apidata.organization(api)
    monkeypatch.setattr(callbacks, "resolve", lambda host, port: ["172.18.0.3"])
    connected = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connected.append(a))
    assert deliver.process(db_conn, clock=api.clock).retrying
    assert connected == []
    assert "not a public address" in delivery_rows(db_conn)[0][3]


def test_outside_dev_the_connection_goes_to_the_checked_address(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(callbacks, "resolve", lambda host, port: ["93.184.216.34"])
    connected = []

    def refuse(address, timeout=None, *args):
        connected.append(address)
        raise ConnectionRefusedError("test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    error = deliver.post("https://listener.example:8443/events", b"{}")
    assert connected == [("93.184.216.34", 8443)]
    assert error is not None and "ConnectionRefusedError" in error


def test_deliveries_go_30_days_after_they_finish_and_events_after_90(
    api, db_conn, migrated_db_url, listener
):
    """The worker's housekeeping (#282) replaced the 30-day purge: a finished delivery goes
    30 days after it finished, its event once it is 90 days old and nothing refers to it.
    A pending delivery is never touched."""
    server = listener()
    hub(api, server.url)
    apidata.organization(api)
    deliver.process(db_conn, clock=api.clock)
    apidata.organization(api, "Pending")  # not delivered yet
    db_conn.commit()

    def housekeeping_after(days):
        day = (api.clock.now() + timedelta(days=days)).astimezone(TZ).date()
        conn = worker_db.connect(migrated_db_url)
        try:
            result = housekeep(
                conn,
                clock=FixedClock(datetime.combine(day, time(4, 10), tzinfo=TZ)),
                tz=TZ,
                pause=lambda _seconds: None,
            )
        finally:
            conn.close()
        # the test client's own session expires too; only the event tables matter here
        return {k: v for k, v in result.deleted.items() if k.startswith("event")}

    assert housekeeping_after(29) == {}
    assert housekeeping_after(32) == {"event_delivery": 1}  # its event is too young
    remaining = db_conn.execute("SELECT state::text FROM event_delivery").fetchall()
    assert remaining == [("pending",)]
    assert db_conn.execute("SELECT count(*) FROM event").fetchone() == (2,)
    assert housekeeping_after(92) == {"event": 1}
    assert db_conn.execute("SELECT count(*) FROM event").fetchone() == (1,)  # the pending one
