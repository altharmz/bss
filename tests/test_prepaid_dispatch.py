"""Real dispatcher posting, rollback and retry eligibility."""

from datetime import time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.clock import FixedClock
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import loop, prepaid
from tests.factories import NOW, product
from tests.test_prepaid_posting import TZ, prepared
from tests.test_prepaid_renewal import renewal
from tests.test_settlement_db import make_payment


def dispatch(conn, now=NOW, **kwargs):
    return prepaid.process_commands(conn, clock=FixedClock(now), tz=TZ, **kwargs)


def test_dispatch_posts_once_and_heartbeats(db_conn):
    _, _, cmd, _ = prepared(db_conn)
    db_conn.commit()
    beats = []
    assert dispatch(db_conn, heartbeat=lambda: beats.append(True)) == [cmd["id"]]
    assert beats == [True]
    assert commands.get(db_conn, cmd["id"])["state"] == "completed"
    assert dispatch(db_conn) == []
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)


def test_dispatch_rolls_back_effects_and_obeys_technical_backoff(db_conn, monkeypatch):
    _, _, cmd, _ = prepared(db_conn)
    db_conn.commit()
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after completion")

    monkeypatch.setattr(commands, "complete", crash)
    assert dispatch(db_conn) == [cmd["id"]]
    failed = commands.get(db_conn, cmd["id"])
    assert failed["state"] == "failedTechnical"
    assert failed["error"] == "after completion"
    assert failed["attempts"] == 1
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM billing_run").fetchone() == (0,)
    monkeypatch.setattr(commands, "complete", original)
    assert dispatch(db_conn, NOW + timedelta(seconds=59)) == []
    assert dispatch(db_conn, NOW + timedelta(minutes=1)) == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["attempts"] == 2


def test_shortfall_remains_pending_and_recovers_on_next_tick(db_conn):
    acc, cmd, _, now = renewal(db_conn, amount="20")
    db_conn.commit()
    assert dispatch(db_conn, now) == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    reason = commands.needs_topup(db_conn, cmd["id"])
    assert reason["required"] > reason["available"]
    assert reason["observed_at"] == now
    assert commands.get(db_conn, cmd["id"])["attempts"] == 0
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    make_payment(db_conn, acc, "20")
    db_conn.commit()
    later = now + timedelta(days=1)
    assert dispatch(db_conn, later) == [cmd["id"]]
    assert commands.needs_topup(db_conn, cmd["id"]) is None
    assert db_conn.execute("SELECT count(*) FROM prepaid_funding_shortfall").fetchone() == (1,)
    assert db_conn.execute(
        "SELECT lower(period) FROM funded_interval WHERE command_id = %s", (cmd["id"],)
    ).fetchone() == (later.date(),)


def test_future_commands_and_stop_checkpoint_are_left_pending(db_conn):
    _, _, cmd, _ = prepared(db_conn)
    db_conn.commit()
    assert dispatch(db_conn, NOW - timedelta(days=1)) == []
    assert dispatch(db_conn, checkpoint=lambda: True) == []
    assert commands.get(db_conn, cmd["id"])["attempts"] == 0


def test_tick_dispatches_even_when_daily_billing_has_a_gap(db_conn, monkeypatch):
    _, _, cmd, _ = prepared(db_conn)
    db_conn.commit()
    gap = (NOW.date() - timedelta(days=5), NOW.date() - timedelta(days=3))
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=gap, dates=[]))
    result = loop.tick(db_conn, clock=FixedClock(NOW), tz=TZ, run_at=time(0, 30))
    assert result.gap == gap
    assert result.prepaid_commands == [cmd["id"]]
    assert commands.get(db_conn, cmd["id"])["state"] == "completed"


def test_one_bad_command_does_not_block_another_account(db_conn):
    _, _, bad, _ = prepared(db_conn, request_changes={"currency": "GBP"})
    _, _, good, _ = prepared(db_conn)
    db_conn.commit()
    assert set(dispatch(db_conn)) == {bad["id"], good["id"]}
    assert commands.get(db_conn, bad["id"])["state"] == "failedTechnical"
    assert commands.get(db_conn, good["id"])["state"] == "completed"


def test_shortfall_does_not_block_later_cheaper_product(db_conn):
    acc, expensive, price, now = renewal(db_conn, amount="20")
    later = now + timedelta(days=1)
    prod = product(db_conn, acc["id"], price["product_offering_id"], later.date())
    request = commands.get(
        db_conn,
        db_conn.execute("SELECT id FROM prepaid_command WHERE kind = 'order'").fetchone()[0],
    )["request"]
    cheap = commands.enqueue(
        db_conn,
        uuid4(),
        acc["id"],
        kind="order",
        due_date=later.date(),
        request=dict(
            request,
            product_id=str(prod["id"]),
            anchor=later.date().isoformat(),
            amount="0",
            taxes=[],
        ),
        now=later,
    )
    funding.reserve_for_command(
        db_conn, acc["id"], Decimal(0), command_id=cheap["id"], held_at=later
    )
    db_conn.commit()
    assert dispatch(db_conn, later) == [expensive["id"], cheap["id"]]
    assert commands.get(db_conn, expensive["id"])["state"] == "pending"
    assert commands.get(db_conn, cheap["id"])["state"] == "completed"
