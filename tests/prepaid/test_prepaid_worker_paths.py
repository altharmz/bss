"""The prepaid worker's stop and race paths for renewals and entitlements (#483), against the
real test database: each ends in the checked commands, attempts and product statuses."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.clock import FixedClock
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import loop
from app.worker import prepaid as worker
from tests.builders.prepaid.prepaid_orders import purchase, shop
from tests.builders.prepaid.prepaid_posting import TZ
from tests.builders.prepaid.prepaid_renewal_producer import tick


@pytest.fixture
def due(api, db_conn, monkeypatch):
    """Two products of one account bought together (funds for one renewal), at their due date."""
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = shop(api, db_conn, funds="36")
    purchase(api, off, acc)
    tick(db_conn, api.clock.now())
    grants = db_conn.execute(
        "SELECT id, product_id, upper(period) FROM funded_interval ORDER BY product_id"
    ).fetchall()
    db_conn.commit()
    return SimpleNamespace(
        acc=acc,
        renewals=[worker.renewal_id(g[0]) for g in grants],
        products=[g[1] for g in grants],
        at=datetime.combine(grants[0][2], time(0, 5), TZ),
    )


def produce(db_conn, now, **kwargs):
    made = worker.produce_renewals(db_conn, clock=FixedClock(now), tz=TZ, **kwargs)
    db_conn.commit()
    return made


def queued(db_conn):
    rows = db_conn.execute(
        "SELECT id FROM prepaid_command WHERE kind = 'renewal' ORDER BY id"
    ).fetchall()
    db_conn.commit()
    return [r[0] for r in rows]


def statuses(db_conn, due):
    rows = dict(db_conn.execute("SELECT id, status FROM product").fetchall())
    db_conn.commit()
    return [rows[p] for p in due.products]


def test_a_checkpoint_stops_production_and_each_account_gets_one_heartbeat(db_conn, due):
    beats = []
    assert (
        produce(db_conn, due.at, checkpoint=lambda: True, heartbeat=lambda: beats.append(1)) == []
    )
    assert queued(db_conn) == [] and beats == []
    assert produce(db_conn, due.at, heartbeat=lambda: beats.append(1)) == due.renewals
    assert queued(db_conn) == sorted(due.renewals) and beats == [1]  # one per account


def test_a_renewal_that_appears_after_discovery_waits_for_the_next_pass(db_conn, due, monkeypatch):
    real = worker.fetch_all

    def first_only(conn, query, params=()):
        rows = real(conn, query, params)
        if query is worker._DUE_RENEWALS and params["account"] is None:
            return rows[:1]  # the second grant expired after the discovery read
        return rows

    monkeypatch.setattr(worker, "fetch_all", first_only)
    # its identity was not locked by this pass: it is not queued without its lock
    assert produce(db_conn, due.at) == due.renewals[:1]
    assert queued(db_conn) == due.renewals[:1]
    monkeypatch.undo()
    assert produce(db_conn, due.at) == due.renewals[1:]
    assert queued(db_conn) == sorted(due.renewals)


def test_a_renewal_another_worker_attempted_after_the_scan_is_not_attempted_again(
    api, db_conn, due, monkeypatch
):
    produce(db_conn, due.at)
    first = due.renewals[0]
    candidates = commands.candidates

    def raced(conn, **scan):
        rows = candidates(conn, **scan)
        commands.fail(conn, first, "the other worker's attempt", now=due.at)  # retry later
        return rows

    monkeypatch.setattr(commands, "candidates", raced)
    attempted = worker.process_commands(db_conn, clock=FixedClock(due.at), tz=TZ)
    db_conn.commit()
    assert first not in attempted
    row = commands.get(db_conn, first)
    db_conn.commit()
    # the other worker's attempt stands: one attempt, its error, its retry time
    assert (row["state"], row["attempts"], row["error"]) == (
        "failedTechnical",
        1,
        "the other worker's attempt",
    )
    assert row["next_retry_at"] > due.at


def test_a_renewal_with_no_previous_interval_records_a_technical_failure(db_conn, due):
    missing = uuid4()
    cid = worker.renewal_id(missing)
    account_id = UUID(due.acc["id"])
    before = funding.load(db_conn, account_id).spendable
    commands.enqueue(
        db_conn,
        cid,
        account_id,
        kind="renewal",
        due_date=due.at.date(),
        request={"previous_interval_id": str(missing)},
        now=due.at,
    )
    db_conn.commit()
    assert worker.process_commands(db_conn, clock=FixedClock(due.at), tz=TZ) == [cid]
    db_conn.commit()
    row = commands.get(db_conn, cid)
    assert (row["state"], row["attempts"], row["error"]) == (
        "failedTechnical",
        1,
        "renewal requires an owned previous interval",
    )
    assert funding.load(db_conn, account_id).spendable == before
    assert db_conn.execute(
        "SELECT count(*) FROM funded_interval WHERE command_id = %s", (cid,)
    ).fetchone() == (0,)


def test_ending_a_product_retires_its_renewal_and_releases_the_owned_hold(db_conn, due):
    produce(db_conn, due.at)
    cid = due.renewals[0]
    account_id = UUID(due.acc["id"])
    held = Decimal("12.00")
    before = funding.load(db_conn, account_id).spendable
    db_conn.commit()
    hold = funding.reserve_for_command(db_conn, account_id, held, command_id=cid, held_at=due.at)
    db_conn.execute("UPDATE product SET status = 'terminated' WHERE id = %s", (due.products[0],))
    db_conn.commit()
    assert funding.load(db_conn, account_id).spendable == before - held
    # Finish the other renewal first, leaving only the ended product's retirement in this pass.
    commands.complete(db_conn, due.renewals[1], {"outcome": "cancelled"}, now=due.at)
    db_conn.commit()
    now = due.at + timedelta(minutes=1)
    assert worker.process_commands(db_conn, clock=FixedClock(now), tz=TZ) == [cid]
    db_conn.commit()
    row = commands.get(db_conn, cid)
    assert (row["state"], row["result"]) == (
        "completed",
        {"outcome": "cancelled", "reason": "productEnded"},
    )
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (now,)
    assert funding.load(db_conn, account_id).spendable == before
    assert db_conn.execute(
        "SELECT count(*) FROM funded_interval WHERE command_id = %s", (cid,)
    ).fetchone() == (0,)


def test_a_checkpoint_stops_the_entitlement_pass_and_each_account_gets_one_heartbeat(db_conn, due):
    # past the paid-through date with no renewal: both lines are suspended for funding
    later = due.at + timedelta(days=1)
    account = [UUID(due.acc["id"])]
    beats = []
    worker.observe_entitlements(
        db_conn, clock=FixedClock(later), tz=TZ, checkpoint=lambda: True, accounts=account
    )
    db_conn.commit()
    assert statuses(db_conn, due) == ["active", "active"]
    worker.observe_entitlements(
        db_conn, clock=FixedClock(later), tz=TZ, heartbeat=lambda: beats.append(1), accounts=account
    )
    db_conn.commit()
    assert statuses(db_conn, due) == ["suspended", "suspended"] and beats == [1]
