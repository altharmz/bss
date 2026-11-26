"""The transfer completion's stop, race and refusal paths (#483), against the real test
database: each ends in a checked transfer status and reason, command state, hold and
balances, and no money moved when the transfer did not complete."""

from decimal import Decimal as D
from uuid import UUID

import pytest

from app.clock import FixedClock
from app.repository import balance_actions
from app.repository import prepaid_commands as commands
from app.worker import prepaid
from tests.builders.prepaid.api_topup import remaining
from tests.builders.prepaid.api_transfer import TRANSFER, pair, post, reserved
from tests.builders.prepaid.prepaid_posting import TZ


def complete(api, db_conn, **kwargs):
    done = prepaid.process_transfers(db_conn, clock=FixedClock(api.clock.now()), tz=TZ, **kwargs)
    db_conn.commit()
    return done


def scripted(monkeypatch, owner, name, change):
    """Replace `owner.name` while the worker runs (`active` is cleared afterwards): the API
    runs in this process, so the reads that check the outcome must see the real one."""
    real = getattr(owner, name)
    active = [True]

    def wrapper(*args, **kwargs):
        return change(real, *args, **kwargs) if active else real(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return active


def status(api, moved):
    return api.get(f"{TRANSFER}/{moved['id']}").json()


def command(db_conn):
    row = db_conn.execute(
        "SELECT state, result->>'outcome', error FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone()
    db_conn.commit()
    return row


def moved_money(db_conn):
    row = db_conn.execute(
        "SELECT (SELECT count(*) FROM funding_consumption),"
        " (SELECT count(*) FROM funding_source WHERE kind = 'transfer')"
    ).fetchone()
    db_conn.commit()
    return row


def test_a_checkpoint_stops_before_the_next_transfer_and_each_gets_one_heartbeat(api, db_conn):
    tx, rx = pair(api, db_conn)
    first = post(api, tx, rx, 10).json()
    second = post(api, tx, rx, 5).json()
    beats = []
    answers = [False, True]
    stop = lambda: answers.pop(0)  # noqa: E731
    assert len(complete(api, db_conn, checkpoint=stop, heartbeat=lambda: beats.append(1))) == 1
    assert beats == [1]
    assert sorted(status(api, t)["status"] for t in (first, second)) == ["completed", "created"]
    assert reserved(api, tx) in (5, 10)  # the other one's hold is still outstanding
    assert len(complete(api, db_conn, heartbeat=lambda: beats.append(1))) == 1 and beats == [1, 1]
    assert {status(api, t)["status"] for t in (first, second)} == {"completed"}
    assert reserved(api, tx) == 0 and remaining(api, rx) == D(23)


def test_a_transfer_completed_by_another_worker_after_the_scan_is_left_alone(
    api, db_conn, monkeypatch
):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()

    def raced(real, conn, cid):
        row = real(conn, cid)
        return None if row is None else {**row, "state": "completed"}

    active = scripted(monkeypatch, commands, "get", raced)
    assert complete(api, db_conn) == []
    active.clear()
    assert status(api, moved)["status"] == "created" and command(db_conn)[0] == "pending"
    assert moved_money(db_conn) == (0, 0) and reserved(api, tx) == 10


def test_a_transfer_whose_hold_is_gone_fails_and_moves_nothing(api, db_conn, monkeypatch):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()

    def released(real, conn, query, params=()):
        if query.startswith("SELECT * FROM funding_hold WHERE id"):
            return None  # as if the hold had been released outside the transfer
        return real(conn, query, params)

    active = scripted(monkeypatch, prepaid, "fetch_one", released)
    complete(api, db_conn)
    active.clear()
    failed = status(api, moved)
    assert failed["status"] == "failed"
    assert "hold is no longer outstanding" in failed["description"]
    assert command(db_conn)[:2] == ("completed", "failed")
    assert reserved(api, tx) == 0 and remaining(api, tx) == D(50)
    assert moved_money(db_conn) == (0, 0) and remaining(api, rx) == D(8)


@pytest.mark.parametrize(
    ("read", "error"),
    [
        (lambda row: {**row, "command_id": UUID(int=0)}, "does not match its action"),
        (lambda row: {**row, "status": "completed"}, "is not awaiting completion"),
        (lambda row: None, "does not match its action"),
    ],
    ids=["other-command", "not-created", "missing"],
)
def test_a_transfer_action_that_no_longer_matches_is_a_technical_failure(
    api, db_conn, monkeypatch, read, error
):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    active = scripted(
        monkeypatch, balance_actions, "get", lambda real, *a, **k: read(real(*a, **k))
    )
    complete(api, db_conn)
    active.clear()
    state, outcome, recorded = command(db_conn)
    assert (state, outcome) == ("failedTechnical", None) and error in recorded
    # nothing changed: the transfer is retried as accepted, its hold intact
    assert status(api, moved)["status"] == "created" and reserved(api, tx) == 10
    assert moved_money(db_conn) == (0, 0) and remaining(api, rx) == D(8)


def test_a_hold_whose_sources_do_not_cover_the_transfer_is_a_technical_failure(
    api, db_conn, monkeypatch
):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()

    class Short:
        def __init__(self, cursor):
            self.rows = [(*r[:3], r[3] - D("0.01")) for r in cursor.fetchall()]

        def fetchall(self):
            return self.rows

    def short_sources(real, query, params=None, **kwargs):
        cursor = real(query, params, **kwargs)
        return Short(cursor) if "FROM funding_hold_source" in str(query) else cursor

    active = scripted(monkeypatch, db_conn, "execute", short_sources)
    complete(api, db_conn)
    active.clear()
    state, _, recorded = command(db_conn)
    assert state == "failedTechnical"
    assert recorded == "the transfer's hold does not cover its principal and fee"
    assert status(api, moved)["status"] == "created" and reserved(api, tx) == 10
    assert moved_money(db_conn) == (0, 0)
