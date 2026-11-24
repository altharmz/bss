"""Command identities coexist with daily runs without entering daily bookkeeping."""

from datetime import timedelta

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors

from alembic import command
from app.repository import billing, worker_requests
from app.repository import prepaid_commands as commands
from tests.builders.prepaid.prepaid_commands import enqueue
from tests.factories import NOW, account, rejected


def command_run(conn, acc):
    row = enqueue(conn, acc)
    return commands.ensure_run(conn, row["id"], run_date=NOW.date(), now=NOW)


def test_same_date_daily_and_multiple_commands_retain_separate_identities(db_conn):
    acc = account(db_conn)
    first, second = command_run(db_conn, acc), command_run(db_conn, acc)
    daily = billing.insert_run(db_conn, NOW.date(), now=NOW, trigger="scheduled")
    db_conn.commit()
    assert len({first["id"], second["id"], daily["id"]}) == 3
    assert billing.get_run(db_conn, NOW.date()) == daily
    assert billing.lock_run(db_conn, NOW.date()) == daily
    assert (
        commands.ensure_run(
            db_conn,
            first["command_id"],
            run_date=NOW.date() + timedelta(days=1),
            now=NOW + timedelta(days=1),
        )
        == first
    )
    with rejected(db_conn, errors.UniqueViolation):
        billing.insert_run(db_conn, NOW.date(), now=NOW, trigger="scheduled")


def test_command_run_rollback_and_concurrent_replay(db_conn, migrated_db_url):
    acc = account(db_conn)
    row = enqueue(db_conn, acc)
    db_conn.commit()
    run = commands.ensure_run(db_conn, row["id"], run_date=NOW.date(), now=NOW)
    with psycopg.connect(migrated_db_url) as other:
        other.execute("SET lock_timeout = '100ms'")
        with pytest.raises(errors.LockNotAvailable):
            commands.ensure_run(other, row["id"], run_date=NOW.date(), now=NOW)
    db_conn.rollback()
    assert billing.get_run_by_id(db_conn, run["id"]) is None
    replacement = commands.ensure_run(db_conn, row["id"], run_date=NOW.date(), now=NOW)
    db_conn.commit()
    with psycopg.connect(migrated_db_url) as other:
        assert commands.ensure_run(other, row["id"], run_date=NOW.date(), now=NOW) == replacement


@pytest.mark.parametrize(
    "status, failure",
    [("running", None), ("failed", "run"), ("failed", "accounts"), ("completed", None)],
)
def test_command_states_never_anchor_daily_queries_or_apis(db_conn, api, status, failure):
    run = command_run(db_conn, account(db_conn))
    if status != "running":
        db_conn.execute(
            "UPDATE billing_run SET status = %s, failure_kind = %s, finished_at = %s WHERE id = %s",
            (status, failure, NOW, run["id"]),
        )
    db_conn.commit()
    assert billing.latest_run_date(db_conn) is None
    assert billing.held_back_by(db_conn) is None
    assert billing.unfinished_run(db_conn) is None
    assert billing.failed_account_runs(db_conn) == []
    assert billing.page_runs(db_conn, offset=0, limit=10) == ([], False)
    assert billing.run_view(db_conn, run["id"]) is None
    assert api.client.get("/billing-status").json()["status"] == "none"
    base = "/internal/v1/billingRun"
    assert api.get(base).json() == []
    for suffix in ("", "/accounts"):
        assert api.get(f"{base}/{run['id']}{suffix}").status_code == 404
    assert api.post(f"{base}/{run['id']}/retry", {}).status_code == 404
    cycles = "/tmf-api/customerBillManagement/v5/billCycle"
    assert api.get(cycles).json() == []
    assert api.get(f"{cycles}/{run['id']}").status_code == 404
    daily = billing.insert_run(
        db_conn, NOW.date() - timedelta(days=1), now=NOW, trigger="scheduled"
    )
    db_conn.commit()
    assert billing.latest_run_date(db_conn) == daily["run_date"]
    rows, more = billing.page_runs(db_conn, offset=0, limit=10)
    assert len(rows) == 1 and not more and rows[0]["id"] == daily["id"] and rows[0]["is_latest"]


def test_command_identity_and_daily_references_are_database_guarded(db_conn):
    acc = account(db_conn)
    run = command_run(db_conn, acc)
    db_conn.commit()
    for statement in (
        "DELETE FROM billing_run",
        "UPDATE billing_run SET run_date = run_date + 1",
        "UPDATE billing_run SET command_id = NULL, kind = 'daily'",
    ):
        with rejected(db_conn, errors.CheckViolation):
            db_conn.execute(statement)
    with rejected(db_conn, errors.CheckViolation):
        billing.record_outcome(db_conn, run["id"], acc["id"], "ok", now=NOW)
    with rejected(db_conn, errors.CheckViolation):
        worker_requests.insert_request(
            db_conn, kind="retry", run_id=run["id"], requested_at=NOW, requested_by=None
        )
    with pytest.raises(LookupError):
        billing.running_run(db_conn, run["id"])
    with pytest.raises(LookupError):
        billing.finish_run(db_conn, run["id"], now=NOW)


def test_newer_command_does_not_hide_daily_on_demand_readiness(api, migrated_db_url):
    from app.worker import db as worker_db
    from app.worker.on_demand import process_demands
    from tests import apidata
    from tests.builders.billing.billing_completion import (
        demand,
        run,
    )
    from tests.timeline import TZ

    acc = apidata.account(api)
    request = demand(api, acc)
    day = api.clock.today() + timedelta(days=1)
    with worker_db.connect(migrated_db_url) as worker:
        assert worker_db.acquire_ownership(worker)
        run(worker, api, day)
        with worker.transaction():
            from uuid import UUID, uuid4

            cmd = commands.enqueue(
                worker,
                uuid4(),
                UUID(acc["id"]),
                kind="order",
                due_date=day,
                request={},
                now=api.clock.now(),
            )
            commands.ensure_run(
                worker, cmd["id"], run_date=day + timedelta(days=1), now=api.clock.now()
            )
        api.clock.set(api.clock.now() + timedelta(days=1))
        assert process_demands(worker, clock=api.clock, tz=TZ)
    assert api.get(request["href"]).json()["state"] == "rejected"


@pytest.mark.migration_history
def test_migration_preserves_daily_history_and_refuses_command_loss(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0038")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        daily = billing.insert_run(conn, NOW.date(), now=NOW, trigger="scheduled")
    command.upgrade(cfg, "0039")
    with psycopg.connect(temp_db_url) as conn:
        assert billing.get_run(conn, NOW.date()) == {**daily, "kind": "daily", "command_id": None}
    command.downgrade(cfg, "0038")
    command.upgrade(cfg, "0039")
    with psycopg.connect(temp_db_url) as conn:
        command_run(conn, acc)
    with pytest.raises(Exception, match="prepaid command run history must be retained"):
        command.downgrade(cfg, "0038")
