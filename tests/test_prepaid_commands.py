"""Journal recovery proofs without enabling a prepaid producer or dispatcher."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors, sql

from alembic import command
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.repository._sql import count_references
from app.repository.parties import ACCOUNT_HISTORY
from tests.builders.prepaid_commands import enqueue
from tests.builders.settlement_db import make_payment
from tests.factories import NOW, account, rejected


def test_committed_request_replay_and_mismatch(db_conn):
    acc = account(db_conn)
    row = enqueue(db_conn, acc)
    db_conn.commit()
    assert enqueue(db_conn, acc, row["id"], now=NOW + timedelta(days=1)) == row
    for changes in (
        {"kind": "renewal"},
        {"due_date": NOW.date() + timedelta(days=1)},
        {"request": {"amount": D("12.35")}},
    ):
        with pytest.raises(ValueError, match="request mismatch"):
            enqueue(db_conn, acc, row["id"], **changes)
    with pytest.raises(ValueError, match="request mismatch"):
        enqueue(db_conn, account(db_conn), row["id"])
    assert db_conn.execute("SELECT request ->> 'amount' FROM prepaid_command").fetchone() == (
        "12.34",
    )


def test_rollback_consumes_no_command_identity(db_conn):
    acc = account(db_conn)
    db_conn.commit()
    row = enqueue(db_conn, acc)
    db_conn.rollback()
    assert commands.get(db_conn, row["id"]) is None
    assert enqueue(db_conn, acc, row["id"], request={"amount": D(20)})["state"] == "pending"


def test_atomic_result_and_hold_conversion_recover_before_and_after_commit(db_conn):
    from tests.builders.funding_conversion import (
        convert,
        prepared,
    )

    w, key, hold, bill = prepared(db_conn)
    enqueue(db_conn, w.account, key)
    db_conn.commit()
    commands.ready(db_conn, key, now=NOW, today=NOW.date())
    convert(db_conn, w, key, bill)
    commands.complete(db_conn, key, {"bill": str(bill["id"])}, now=NOW)
    db_conn.rollback()  # Process death before commit leaves both writes absent.
    assert commands.get(db_conn, key)["state"] == "pending"
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)
    commands.ready(db_conn, key, now=NOW, today=NOW.date())
    assert convert(db_conn, w, key, bill) == hold
    result = commands.complete(db_conn, key, {"bill": str(bill["id"])}, now=NOW)
    db_conn.commit()
    assert commands.ready(db_conn, key, now=NOW, today=NOW.date()) == result
    assert commands.complete(db_conn, key, {"ignored": True}, now=NOW) == result
    assert commands.fail(db_conn, key, "late error", now=NOW) == result
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (1,)


def test_technical_backoff_manual_retry_and_hold_retention(db_conn):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    row = enqueue(db_conn, acc)
    key = row["id"]
    hold = funding.reserve_for_command(db_conn, acc["id"], D(10), command_id=key, held_at=NOW)
    db_conn.commit()
    at = NOW
    for attempt, minutes in enumerate((1, 5, 60, 60), 1):
        failed = commands.fail(db_conn, key, "broken posting", now=at)
        assert failed["attempts"] == attempt
        assert failed["next_retry_at"] == at + timedelta(minutes=minutes)
        with pytest.raises(ValueError, match="not due"):
            commands.ready(db_conn, key, now=at, today=at.date())
        at = failed["next_retry_at"]
        assert commands.ready(db_conn, key, now=at, today=at.date()) == failed
    queued = commands.retry(db_conn, key, now=at)
    assert queued["id"] == key and queued["attempts"] == 4
    assert queued["state"] == "pending" and queued["next_retry_at"] is None
    assert funding.load(db_conn, acc["id"]).spendable == D(10)
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (None,)
    with pytest.raises(ValueError, match="only failedTechnical"):
        commands.retry(db_conn, key, now=at)
    completed = commands.complete(db_conn, key, {"outcome": "insufficientFunds"}, now=at)
    assert completed["state"] == "completed" and completed["attempts"] == 5


def test_due_date_and_chronology_are_enforced(db_conn):
    row = enqueue(db_conn, account(db_conn), due_date=NOW.date() + timedelta(days=1))
    with pytest.raises(ValueError, match="not due"):
        commands.ready(db_conn, row["id"], now=NOW, today=NOW.date())
    with pytest.raises(ValueError, match="precedes"):
        commands.fail(db_conn, row["id"], "error", now=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="unknown"):
        commands.retry(db_conn, uuid4(), now=NOW)


def test_funding_decisions_survive_recovery_and_enforce_chronology(db_conn):
    row = enqueue(db_conn, account(db_conn))
    key = row["id"]
    commands.fail(db_conn, key, "broken", now=NOW)
    at = NOW + timedelta(minutes=1)
    commands.record_shortfall(db_conn, key, required=D(20), available=D(5), now=at)
    db_conn.commit()
    current = commands.get(db_conn, key)
    assert current["state"] == "pending"
    assert current["attempts"] == 1
    assert current["error"] is None and current["next_retry_at"] is None
    commands.record_shortfall(db_conn, key, required=D(20), available=D(5), now=at)
    with pytest.raises(ValueError, match="mismatch"):
        commands.record_shortfall(db_conn, key, required=D(21), available=D(5), now=at)
    with pytest.raises(ValueError, match="precedes"):
        commands.complete(db_conn, key, {}, now=NOW)
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute("DELETE FROM prepaid_funding_shortfall")
    commands.complete(db_conn, key, {"ok": True}, now=at)
    assert commands.needs_topup(db_conn, key) is None
    assert db_conn.execute("SELECT count(*) FROM prepaid_funding_shortfall").fetchone() == (1,)


def test_shortfall_rollback_and_latest_observation(db_conn):
    row = enqueue(db_conn, account(db_conn))
    db_conn.commit()
    key = row["id"]
    commands.record_shortfall(db_conn, key, required=D(20), available=D(5), now=NOW)
    db_conn.rollback()
    assert commands.needs_topup(db_conn, key) is None
    for offset, amount in enumerate((D(5), D(10))):
        commands.record_shortfall(
            db_conn, key, required=D(20), available=amount, now=NOW + timedelta(minutes=offset)
        )
    assert commands.needs_topup(db_conn, key)["available"] == D(10)


@pytest.mark.migration_history
def test_shortfall_migration_preserves_commands_and_refuses_history_loss(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0041")
    with psycopg.connect(temp_db_url) as conn:
        row = enqueue(conn, account(conn))
    command.upgrade(cfg, "0042")
    command.downgrade(cfg, "0041")
    command.upgrade(cfg, "0042")
    with psycopg.connect(temp_db_url) as conn:
        assert commands.get(conn, row["id"]) == row
        commands.record_shortfall(conn, row["id"], required=D(20), available=D(5), now=NOW)
    with pytest.raises(Exception, match="funding decisions must be retained"):
        command.downgrade(cfg, "0041")


def test_concurrent_journal_and_hold_retries_share_identity_lock(db_conn, migrated_db_url):
    acc = account(db_conn)
    db_conn.commit()
    row = enqueue(db_conn, acc)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            enqueue(other, acc, row["id"])
        other.rollback()
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            funding.reserve_for_command(other, acc["id"], D(0), command_id=row["id"], held_at=NOW)
        other.rollback()
        db_conn.commit()
        assert enqueue(other, acc, row["id"]) == row


def test_history_guards_without_bucket_or_hold(db_conn):
    acc = account(db_conn)
    row = enqueue(db_conn, acc)
    db_conn.commit()
    assert count_references(db_conn, acc["id"], ACCOUNT_HISTORY)["prepaidCommand"] == 1
    for statement in (
        "DELETE FROM prepaid_command",
        "UPDATE prepaid_command SET request = '{}'",
        "UPDATE prepaid_command SET due_date = due_date + 1",
        "UPDATE billing_account SET rating_type = 'prepaid'",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.ForeignKeyViolation):
        db_conn.execute("DELETE FROM billing_account WHERE id = %s", (acc["id"],))
    commands.complete(db_conn, row["id"], {"outcome": "done"}, now=NOW)
    db_conn.commit()
    for statement in (
        "UPDATE prepaid_command SET result = '{}'",
        "UPDATE prepaid_command SET state = 'pending', result = NULL",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)


@pytest.mark.parametrize("hold_first", [True, False])
def test_journal_and_hold_must_share_account(db_conn, hold_first):
    acc, other = account(db_conn), account(db_conn)
    key = uuid4()
    db_conn.commit()
    with rejected(db_conn, errors.CheckViolation):
        if hold_first:
            funding.reserve_for_command(db_conn, other["id"], D(0), command_id=key, held_at=NOW)
        enqueue(db_conn, acc, key)
        if not hold_first:
            funding.reserve_for_command(db_conn, other["id"], D(0), command_id=key, held_at=NOW)
        db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "assignment",
    [
        "state = 'completed'",
        "state = 'failedTechnical', error = 'bad', attempts = 1, attempted_at = created_at",
        "attempts = -1",
        "kind = 'unknown'",
    ],
)
def test_invalid_journal_state_is_rejected(db_conn, assignment):
    enqueue(db_conn, account(db_conn))
    db_conn.commit()
    with rejected(db_conn, errors.CheckViolation):
        db_conn.execute(sql.SQL("UPDATE prepaid_command SET {} ").format(sql.SQL(assignment)))


@pytest.mark.migration_history
def test_migration_preserves_legacy_funding_and_refuses_history_loss(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0037")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        make_payment(conn, acc, "20")
        hold = funding.reserve(conn, acc["id"], D(5), held_at=NOW)
    command.upgrade(cfg, "0038")
    command.downgrade(cfg, "0037")
    command.upgrade(cfg, "0038")
    with psycopg.connect(temp_db_url) as conn:
        assert funding.load(conn, acc["id"]).spendable == D(15)
        assert conn.execute("SELECT id FROM funding_hold").fetchone() == (hold,)
        enqueue(conn, acc)
    with pytest.raises(Exception, match="prepaid command history must be retained"):
        command.downgrade(cfg, "0037")
