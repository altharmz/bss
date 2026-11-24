"""Round trips the worker's per-account paths no longer make (#305, docs/billing-worker.md
"Round trips"): the balance-ledger schema check is asked once per connection, the prepaid
passes read the lifecycle settings once per pass and each account's row once, and the
balance ledger check runs once per account and transaction - while it still refuses
every write that breaks the bounds."""
# ruff: noqa: F811

import re
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest
from alembic.config import Config
from psycopg import errors

from alembic import command
from app.billing.adapter import RunPrices
from app.repository import billing
from app.worker import db as worker_db
from app.worker.lifecycle import process_lifecycle
from app.worker.notify import process_notifications
from app.worker.prepaid import observe_entitlements
from app.worker.process import DateRun
from app.worker.runs import start_run
from tests import apidata
from tests.builders.billing.settlement_db import make_payment
from tests.builders.perf.worker_run_cost import DEC4
from tests.builders.prepaid.balance_actions import action, check, credit, prepaid, topup
from tests.builders.prepaid.prepaid_lifecycle import SimpleNamespaceWorld
from tests.builders.prepaid.prepaid_orders import shop
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import buy as buy_line
from tests.builders.resources.network_provisioning import make_line, stub  # noqa: F401 - fixtures
from tests.builders.worker.worker_process import at, outcome, scenario
from tests.perf.guard import counting
from tests.timeline import TZ

SCHEMA_CHECK = "to_regclass('funding_consumption')"
SETTINGS_READ = re.compile(r"FROM prepaid_lifecycle_setting")
ACCOUNT_READ = re.compile(r"FROM (billing_account|.*Identifier\('billing_account'\)).*WHERE id = ")


# --- the schema check ---


def test_the_ledger_schema_check_is_asked_once_per_connection(
    db_conn, migrated_db_url, monkeypatch
):
    acc = prepaid(db_conn)
    with counting(monkeypatch) as seen:
        for _ in range(3):
            billing.account_balance(db_conn, acc.id)
    assert sum(SCHEMA_CHECK in q for q in seen) <= 1  # asked by an earlier call, or here
    with psycopg.connect(migrated_db_url) as other, counting(monkeypatch) as seen:
        for _ in range(3):
            billing.account_balance(other, acc.id)
    assert sum(SCHEMA_CHECK in q for q in seen) == 1  # a new connection asks again, once


def test_an_older_schema_is_asked_again_and_a_later_migration_is_seen(temp_db_url, monkeypatch):
    """A no is never remembered: the same live connection sees 0045 once it is applied."""
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0044")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        with counting(monkeypatch) as seen:
            assert (billing.has_balance_ledger(conn), billing.has_balance_ledger(conn)) == (
                False,
                False,
            )
        assert sum(SCHEMA_CHECK in q for q in seen) == 2
        command.upgrade(cfg, "0045")
        assert billing.has_balance_ledger(conn) is True
        with counting(monkeypatch) as seen:
            assert billing.has_balance_ledger(conn) is True
        assert not [q for q in seen if SCHEMA_CHECK in q]


# --- the prepaid passes ---


@pytest.fixture
def two_lines(api, db_conn, stub):
    """Two prepaid accounts with one numbered line each."""
    off, first = shop(api, db_conn, funds="12", amount=10, numbers=True)
    make_line(api, off)
    second = apidata.account(api, name="Second")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (second["id"],)
    )
    make_payment(db_conn, dict(second, currency="USD"), "12")
    db_conn.commit()
    for acc, value in ((first, "12125550177"), (second, "12125550178")):
        buy_line(api, SimpleNamespaceWorld(off, acc), value)
    tick(db_conn, api.clock.now())
    lines = db_conn.execute("SELECT count(*) FROM funded_interval").fetchone()[0]
    assert lines == 2
    return [UUID(first["id"]), UUID(second["id"])]


def test_the_passes_read_the_settings_once_and_each_account_once(
    api, db_conn, two_lines, monkeypatch
):
    passes = (process_lifecycle, observe_entitlements, process_notifications)
    for run in passes:
        reads = []
        for accounts in (two_lines[:1], two_lines):
            with counting(monkeypatch) as seen:
                run(db_conn, clock=api.clock, tz=TZ, accounts=accounts)
            db_conn.commit()
            account_reads = [q for q in seen if ACCOUNT_READ.search(q)]
            assert len(account_reads) == len(accounts), (run.__name__, account_reads)
            assert all("FOR UPDATE" in q for q in account_reads), run.__name__
            reads.append(sum(bool(SETTINGS_READ.search(q)) for q in seen))
        assert reads[0] == reads[1] <= 2, (run.__name__, reads)  # per pass, not per account


# --- the ledger check ---


def ledger_checks(conn) -> int:
    """balance_ledger_check calls in the current transaction (track_functions = pl)."""
    return conn.execute(
        "SELECT coalesce(pg_stat_get_xact_function_calls('balance_ledger_check'::regproc), 0)"
    ).fetchone()[0]


def test_the_ledger_check_runs_once_per_account_and_transaction(db_conn):
    acc, other = prepaid(db_conn), prepaid(db_conn, "B")
    db_conn.commit()
    db_conn.execute("SET track_functions = 'pl'")
    with db_conn.transaction():
        topup(db_conn, acc, "5")  # a payment and its action
        credit(db_conn, acc, "3")  # an action and its source
        credit(db_conn, other, "2")
        before = ledger_checks(db_conn)
        check(db_conn)
        assert ledger_checks(db_conn) - before == 2  # five rows, two accounts


def test_a_write_after_a_passed_check_is_checked_again(db_conn):
    """The note of checked accounts is cleared by the next write statement, so a broken
    ledger written after an immediate check in the same transaction is still refused."""
    acc = prepaid(db_conn)
    db_conn.commit()
    with pytest.raises(errors.CheckViolation, match="ledger entries"), db_conn.transaction():
        credit(db_conn, acc, "5")
        check(db_conn)  # passes, and notes the account
        action(db_conn, acc, "adjustment", "5")  # a credit without its source
        check(db_conn)
    with pytest.raises(errors.CheckViolation, match="combined bounds"), db_conn.transaction():
        credited = credit(db_conn, acc, "5")
        check(db_conn)
        debited = action(db_conn, acc, "adjustment", "-6")
        source_id = db_conn.execute(
            "SELECT id FROM funding_source WHERE action_id = %s", (credited,)
        ).fetchone()[0]
        db_conn.execute(
            "INSERT INTO funding_consumption"
            " (billing_account_id, action_id, funding_source_id, amount, consumed_at)"
            " VALUES (%s, %s, %s, %s, now())",
            (acc.id, debited, source_id, D(6)),
        )
    # and at commit, without an immediate check in between
    with pytest.raises(errors.CheckViolation, match="ledger entries"):
        with db_conn.transaction():
            credit(db_conn, acc, "5")
            action(db_conn, acc, "adjustment", "5")


# --- the run's prices ---

PRICE_READ = re.compile(r"FROM product_offering_price\s+WHERE product_offering_id = ANY")


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def test_a_run_reads_each_offerings_prices_once(api, worker, monkeypatch):
    """a and b share an offering, x has its own: two price reads for three accounts."""
    s = scenario(api, worker, broken=False)
    api.clock.set(at(DEC4, 0, 30))
    started = start_run(worker, DEC4, now=api.clock.now(), trigger="scheduled", tz=TZ.key)
    date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    with counting(monkeypatch) as seen:
        date_run.run_all()
    date_run.close()
    assert sum(bool(PRICE_READ.search(q)) for q in seen) == 2
    assert [outcome(worker, DEC4, i)["status"] for i in s["ids"].values()] == ["ok"] * 3


def test_the_run_prices_are_the_prices_read_directly(api, worker):
    s = scenario(api, worker, broken=False)
    offerings = [
        row[0]
        for row in worker.execute("SELECT DISTINCT product_offering_id FROM product").fetchall()
    ]
    assert len(offerings) == 2
    prices = RunPrices()
    for wanted in ([offerings[0]], offerings, [offerings[1], offerings[0], offerings[1]], []):
        assert prices.load(worker, wanted) == billing.load_recurring_prices(worker, wanted)
    assert s["ids"]
