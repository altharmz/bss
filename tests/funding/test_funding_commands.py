"""Command retries retain one hold through commit, rollback and release."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors

from alembic import command
from app.billing.funding import InsufficientFunds
from app.repository import funding
from tests.builders.billing.settlement_db import make_payment
from tests.factories import NOW, account, rejected


def test_command_retry_after_commit_and_release(db_conn):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    key = uuid4()
    hold = funding.reserve_for_command(db_conn, acc["id"], D("15"), command_id=key, held_at=NOW)
    db_conn.commit()
    assert (
        funding.reserve_for_command(
            db_conn, acc["id"], D("15"), command_id=key, held_at=NOW + timedelta(days=1)
        )
        == hold
    )
    for owner in (None, uuid4()):
        with pytest.raises(ValueError, match="owner mismatch"):
            funding.release(db_conn, acc["id"], hold, released_at=NOW, command_id=owner)
    funding.release(db_conn, acc["id"], hold, released_at=NOW, command_id=key)
    db_conn.commit()
    assert (
        funding.reserve_for_command(db_conn, acc["id"], D("15"), command_id=key, held_at=NOW)
        == hold
    )
    assert funding.load(db_conn, acc["id"]).spendable == D("20")
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (1,)


def test_command_conflicting_account_or_amount(db_conn):
    acc = account(db_conn)
    other = account(db_conn, name="Other")
    make_payment(db_conn, acc, "20")
    key = uuid4()
    funding.reserve_for_command(db_conn, acc["id"], D("10"), command_id=key, held_at=NOW)
    for owner, amount in ((other["id"], D("10")), (acc["id"], D("11"))):
        with pytest.raises(ValueError, match="request mismatch"):
            funding.reserve_for_command(db_conn, owner, amount, command_id=key, held_at=NOW)
    assert funding.load(db_conn, acc["id"]).spendable == D("10")


def test_command_rollback_does_not_consume_identity(db_conn):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    db_conn.commit()
    key = uuid4()
    first = funding.reserve_for_command(db_conn, acc["id"], D("10"), command_id=key, held_at=NOW)
    db_conn.rollback()
    second = funding.reserve_for_command(db_conn, acc["id"], D("12"), command_id=key, held_at=NOW)
    assert first != second
    assert funding.load(db_conn, acc["id"]).spendable == D("8")


def test_command_shortfall_does_not_claim_identity(db_conn):
    acc = account(db_conn)
    key = uuid4()
    with pytest.raises(InsufficientFunds):
        funding.reserve_for_command(db_conn, acc["id"], D("10"), command_id=key, held_at=NOW)
    assert db_conn.execute("SELECT count(*) FROM funding_command_hold").fetchone() == (0,)
    make_payment(db_conn, acc, "10")
    funding.reserve_for_command(db_conn, acc["id"], D("10"), command_id=key, held_at=NOW)
    assert funding.load(db_conn, acc["id"]).spendable == 0


def test_command_identity_and_ownership_are_retained(db_conn):
    acc = account(db_conn)
    other = account(db_conn, name="Other")
    key = uuid4()
    hold = funding.reserve_for_command(db_conn, acc["id"], D(0), command_id=key, held_at=NOW)
    db_conn.commit()
    for statement in (
        "DELETE FROM funding_command_hold",
        "UPDATE funding_command_hold SET command_id = gen_random_uuid()",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.UniqueViolation):
        db_conn.execute(
            "INSERT INTO funding_command_hold VALUES (%s, %s, %s)",
            (uuid4(), hold, acc["id"]),
        )
    unowned = funding.reserve(db_conn, acc["id"], D(0), held_at=NOW)
    with rejected(db_conn, errors.ForeignKeyViolation):
        db_conn.execute(
            "INSERT INTO funding_command_hold VALUES (%s, %s, %s)",
            (uuid4(), unowned, other["id"]),
        )


def test_command_duplicates_serialize_before_account_lock(db_conn, migrated_db_url):
    acc = account(db_conn)
    make_payment(db_conn, acc, "20")
    db_conn.commit()
    key = uuid4()
    hold = funding.reserve_for_command(db_conn, acc["id"], D("15"), command_id=key, held_at=NOW)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            funding.reserve_for_command(other, acc["id"], D("15"), command_id=key, held_at=NOW)
        other.rollback()
        db_conn.commit()
        assert (
            funding.reserve_for_command(other, acc["id"], D("15"), command_id=key, held_at=NOW)
            == hold
        )
        assert funding.load(other, acc["id"]).spendable == D("5")


@pytest.mark.migration_history
def test_command_ownership_migration_round_trip_and_retention(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0035")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        legacy = funding.reserve(conn, acc["id"], D(0), held_at=NOW)
    command.upgrade(cfg, "0036")
    command.downgrade(cfg, "0035")
    command.upgrade(cfg, "0036")
    with psycopg.connect(temp_db_url) as conn:
        funding.release(conn, acc["id"], legacy, released_at=NOW)
        key = uuid4()
        hold = funding.reserve_for_command(conn, acc["id"], D(0), command_id=key, held_at=NOW)
        funding.release(conn, acc["id"], hold, released_at=NOW, command_id=key)
    with pytest.raises(Exception, match="funding command ownership must be retained"):
        command.downgrade(cfg, "0035")
