"""The worker process's wake-up failures (app/worker/__main__.py, #511): a database that went
away closes the connection and the next wake-up connects again; any other failure keeps the
connection. `--once` exits 1 after a failed wake-up, the loop goes on to the next one."""

import logging
import signal
from types import SimpleNamespace

import psycopg
import pytest

from app.worker import __main__ as worker_main
from tests.builders.worker.logs import worker_lines


class Conn:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def process(monkeypatch, tmp_path):
    """main() without its start checks, log setup and DB poller; `connected` lists every
    connection it opened."""
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    connected: list[Conn] = []

    def connect() -> Conn:
        connected.append(Conn(f"conn{len(connected) + 1}"))
        return connected[-1]

    monkeypatch.setattr(worker_main.db, "connect", connect)
    monkeypatch.setattr(worker_main, "check_run_at", lambda value: None)
    monkeypatch.setattr(
        worker_main, "clock_from_environment", lambda: SimpleNamespace(now=lambda: None)
    )
    monkeypatch.setattr(worker_main.logsetup, "configure", lambda name: None)
    monkeypatch.setattr(worker_main.logsetup, "start_db_poller", lambda now: None)
    monkeypatch.setattr(worker_main, "WAKE_SECONDS", 0)
    monkeypatch.setenv(worker_main.LIVENESS, str(tmp_path / "alive"))
    yield SimpleNamespace(connected=connected, liveness=tmp_path / "alive")
    for number, handler in handlers.items():
        signal.signal(number, handler)
    worker_main.logsetup.request_id.set(None)


def ticking(monkeypatch, *outcomes):
    """Each wake-up takes the next outcome: an exception is raised, anything else returned.
    The wake-up after the last one sends the process SIGTERM (the loop then stops)."""
    seen: list[str] = []

    def tick(conn, **kwargs):
        seen.append(conn.name)
        if len(seen) > len(outcomes):
            signal.raise_signal(signal.SIGTERM)
            return "stopping"
        outcome = outcomes[len(seen) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(worker_main, "tick", tick)
    return seen


def test_once_exits_1_after_an_unreachable_database_and_closes_the_connection(
    process, monkeypatch, caplog
):
    seen = ticking(monkeypatch, psycopg.OperationalError("server closed the connection"))
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        assert worker_main.main(["--once"]) == 1
    assert seen == ["conn1"]
    assert [c.closed for c in process.connected] == [True]
    assert worker_lines(caplog) == ["database unreachable; trying again at the next wake-up"]
    assert not process.liveness.exists()  # a failed wake-up is not a sign of life


def test_once_exits_1_after_a_failed_wake_up_and_keeps_the_connection_until_exit(
    process, monkeypatch, caplog
):
    seen = ticking(monkeypatch, ValueError("a bug in one pass"))
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        assert worker_main.main(["--once"]) == 1
    assert seen == ["conn1"]
    assert worker_lines(caplog) == ["wake-up failed"]
    assert [c.closed for c in process.connected] == [True]  # closed on exit only


def test_the_loop_reconnects_after_a_lost_database_and_recovers_after_a_failure(
    process, monkeypatch
):
    seen = ticking(
        monkeypatch,
        psycopg.OperationalError("server closed the connection"),
        ValueError("a bug in one pass"),
        "ok",
    )
    assert worker_main.main([]) == 0  # stopped by SIGTERM, not by --once: exit 0
    # the lost connection is replaced; the failed pass keeps the new one
    assert seen == ["conn1", "conn2", "conn2", "conn2"]
    assert [c.closed for c in process.connected] == [True, True]
    assert process.liveness.exists()
