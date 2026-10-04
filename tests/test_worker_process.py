"""Billing dates account by account (app/worker/process.py): outcomes, the "completed"
rule, failure isolation, in-order catch-up after a fix, per-account atomicity.

Scenarios run through the real API and the real worker, twice: in the test database with
a failure, and in a separate control database where nothing fails. The account that
failed must end with exactly the charges and bills of the control - which also catches a
catch-up in the wrong order (a termination effective Dec 3, ordered Dec 2, gives a charge
and a refund when the dates are billed one by one, a single reduced charge otherwise).

A price is "broken" by switching its currency (the account bills in RUB, so MissingPrice -
a BillingError) and fixed by switching it back.
"""

import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from app.billing.model import MissingPrice
from app.clock import FixedClock
from app.config import settings
from app.repository import billing
from app.worker import db as worker_db
from app.worker import process
from app.worker.process import DateRun, bill_date, retry_run
from app.worker.runs import start_run
from tests import apidata, timeline
from tests.conftest import _create_database, _drop_database
from tests.factories import MSK, account, rejected
from tests.timeline import TZ, iso, midnight

NOV20, DEC1, DEC2, DEC3 = (
    date(2026, 11, 20),
    date(2026, 12, 1),
    date(2026, 12, 2),
    date(2026, 12, 3),
)


def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return midnight(day) + timedelta(hours=hour, minutes=minute)


# --- fixtures: a worker connection, a control database ---


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def control_db_url(template_db):
    name = f"vpslab_test_{uuid.uuid4().hex[:8]}"
    url = _create_database(name, template=template_db)
    try:
        yield url
    finally:
        _drop_database(name)


@contextmanager
def app_on(url: str):
    """Point the in-process app (the `api` client) at another database for a while."""
    previous = settings.database_url
    settings.database_url = url
    try:
        yield
    finally:
        settings.database_url = previous


# --- the scenario ---


def add(api, acc, offering, start, *, on):
    api.clock.set(on)
    placed = apidata.order(
        api, acc, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def terminate(api, acc, product, effective, *, on):
    api.clock.set(on)
    placed = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(effective))
    )
    assert placed["state"] == "completed", placed


def set_currency(conn, offering_id, currency):
    conn.execute(
        "UPDATE product_offering_price SET currency = %s WHERE product_offering_id = %s",
        (currency, offering_id),
    )


def run(conn, clock: FixedClock, day: date):
    clock.set(at(day, 0, 30))
    return bill_date(conn, day, clock=clock, tz=TZ, trigger="scheduled")


FirstRun = Callable[[psycopg.Connection, FixedClock, dict], psycopg.Connection]


def plain_first_run(conn, clock, ids):
    run(conn, clock, DEC1)
    return conn


def scenario(api, conn, *, broken: bool, first_run: FirstRun = plain_first_run) -> dict:
    """Accounts a and b on a healthy offering, x on a fragile one, all from Dec 1; x's
    product is terminated as of Dec 3 (ordered Dec 2). Runs Dec 1, 2, 3. `first_run`
    performs Dec 1 and returns the connection that carries on (a takeover replaces it)."""
    api.clock.set(timeline.NOW)  # prices can only start after "now": create them first
    healthy = apidata.sellable_offering(api, value=300)
    fragile = apidata.sellable_offering(api, value=500)
    api.clock.set(at(NOV20, 9))
    a, b, x = apidata.account(api), apidata.account(api), apidata.account(api)
    add(api, a, healthy, DEC1, on=at(NOV20))
    add(api, b, healthy, DEC1, on=at(NOV20))
    product = add(api, x, fragile, DEC1, on=at(NOV20))
    ids = {name: UUID(acc["id"]) for name, acc in (("a", a), ("b", b), ("x", x))}
    if broken:
        set_currency(conn, fragile["id"], "EUR")
    conn = first_run(conn, api.clock, ids)
    run(conn, api.clock, DEC2)
    terminate(api, x, product, DEC3, on=at(DEC2))
    run(conn, api.clock, DEC3)
    runs = {day: billing.get_run(conn, day) for day in (DEC1, DEC2, DEC3)}
    return {"ids": ids, "runs": runs, "fragile": fragile, "conn": conn}


def ledger(conn, account_id) -> tuple[list, list]:
    """An account's charges (by run date) and bills, without ids - comparable across
    databases."""
    charges = conn.execute(
        """
        SELECT r.run_date, a.charge_type::text, a.amount, lower(a.period), upper(a.period),
               a.customer_bill_id IS NOT NULL
          FROM applied_customer_billing_rate a JOIN billing_run r ON r.id = a.billing_run_id
         WHERE a.billing_account_id = %s ORDER BY 1, 2, 4
        """,
        (account_id,),
    ).fetchall()
    bills = conn.execute(
        "SELECT bill_date, lower(bill_period), upper(bill_period), amount_due FROM customer_bill"
        " WHERE billing_account_id = %s ORDER BY 1",
        (account_id,),
    ).fetchall()
    return charges, bills


def outcome(conn, day, account_id):
    return billing.outcomes(conn, billing.get_run(conn, day)["id"]).get(account_id)


@pytest.fixture
def control(api, control_db_url):
    """The scenario without any failure, in the control database: x's ledger."""
    with app_on(control_db_url):
        conn = worker_db.connect(control_db_url)
        try:
            s = scenario(api, conn, broken=False)
            statuses = {day: r["status"] for day, r in s["runs"].items()}
            assert statuses == {DEC1: "completed", DEC2: "completed", DEC3: "completed"}
            charges, bills = ledger(conn, s["ids"]["x"])
        finally:
            conn.close()
    # the control is not trivial: a charge on Dec 1 and a refund on Dec 3
    assert [(c[0], c[1]) for c in charges] == [(DEC1, "recurring"), (DEC3, "refund")]
    return charges, bills


# --- failure isolation and in-order catch-up ---


@pytest.mark.capability("worker.failureIsolation")
def test_a_failed_account_blocks_only_itself_and_catches_up_in_order(api, worker, control):
    s = scenario(api, worker, broken=True)
    a, b, x = s["ids"]["a"], s["ids"]["b"], s["ids"]["x"]

    # Dec 1: x failed, the run failed because of accounts, a and b billed
    first = billing.get_run(worker, DEC1)
    assert (first["status"], first["failure_kind"]) == ("failed", "accounts")
    assert first["error"] == "1 of 3 accounts not billed (1 failed)"
    assert outcome(worker, DEC1, x)["status"] == "failed"
    assert outcome(worker, DEC1, x)["error"].startswith("MissingPrice: ")
    assert [outcome(worker, DEC1, i)["status"] for i in (a, b)] == ["ok", "ok"]
    # Dec 2 and 3: x blocked, waiting for Dec 1 - not billed; the others go ahead
    for day in (DEC2, DEC3):
        row = outcome(worker, day, x)
        assert (row["status"], row["waits_for_run_id"]) == ("blocked", first["id"])
        assert [outcome(worker, day, i)["status"] for i in (a, b)] == ["ok", "ok"]
        assert billing.get_run(worker, day)["failure_kind"] == "accounts"
    assert ledger(worker, x) == ([], [])
    others = {i: (ledger(worker, i), outcome(worker, DEC1, i)) for i in (a, b)}

    # the fix (a reviewed one-off script in real life), then a retry of Dec 1
    set_currency(worker, s["fragile"]["id"], "RUB")
    api.clock.set(at(DEC3, 12))
    retry_run(worker, first["id"], clock=api.clock, tz=TZ)

    for day in (DEC1, DEC2, DEC3):
        assert billing.get_run(worker, day)["status"] == "completed"
        assert (outcome(worker, day, x)["status"], outcome(worker, day, x)["attempts"]) == (
            "ok",
            2,
        )
    # x ends exactly as if nothing had failed: same charges per run date, same bills
    assert ledger(worker, x) == control
    # the retry touched only x: a and b keep their charges and their outcome rows
    for i in (a, b):
        assert (ledger(worker, i), outcome(worker, DEC1, i)) == others[i]


@pytest.mark.capability("worker.failureIsolation")
def test_retrying_a_later_run_leaves_accounts_blocked_on_an_earlier_date(api, worker):
    s = scenario(api, worker, broken=True)
    x = s["ids"]["x"]
    set_currency(worker, s["fragile"]["id"], "RUB")  # fixed, but Dec 1 is not retried
    retry_run(worker, s["runs"][DEC2]["id"], clock=api.clock, tz=TZ)
    assert outcome(worker, DEC2, x)["status"] == "blocked"  # still waiting for Dec 1
    assert billing.get_run(worker, DEC2)["status"] == "failed"
    assert ledger(worker, x) == ([], [])


@pytest.mark.capability("worker.failureIsolation")
def test_a_catch_up_stops_at_the_next_failure_and_later_dates_wait_for_it(api, worker, monkeypatch):
    s = scenario(api, worker, broken=True)
    x = s["ids"]["x"]
    set_currency(worker, s["fragile"]["id"], "RUB")
    real = process.bill_account

    def fails_on_dec2(conn, account_id, run, **kw):
        if account_id == x and run["run_date"] == DEC2:
            raise MissingPrice(uuid.uuid4(), DEC2, "RUB")  # injected for Dec 2 only
        return real(conn, account_id, run, **kw)

    monkeypatch.setattr(process, "bill_account", fails_on_dec2)
    retry_run(worker, s["runs"][DEC1]["id"], clock=api.clock, tz=TZ)
    assert outcome(worker, DEC1, x)["status"] == "ok"
    assert billing.get_run(worker, DEC1)["status"] == "completed"
    assert outcome(worker, DEC2, x)["status"] == "failed"
    dec3 = outcome(worker, DEC3, x)
    assert (dec3["status"], dec3["waits_for_run_id"]) == ("blocked", s["runs"][DEC2]["id"])


def test_only_a_failed_run_can_be_retried(api, worker):
    s = scenario(api, worker, broken=False)
    with pytest.raises(LookupError, match="not failed"):
        retry_run(worker, s["runs"][DEC1]["id"], clock=api.clock, tz=TZ)


# --- per-account atomicity ---


@pytest.mark.capability("worker.takeover")
def test_charges_and_outcome_commit_together(api, worker, control, migrated_db_url, monkeypatch):
    """The worker dies (its backend is terminated) after x's charges are written but before
    x's outcome row: neither stays, the run stays running with its lock gone, and a second
    worker takes it over and bills x exactly once - as in the control."""
    seen = {}
    second = None

    def dies_then_taken_over(conn, clock, ids):
        nonlocal second
        x, real = ids["x"], billing.record_outcome

        def dies_before_the_row(c, run_id, account_id, status, **kw):
            if account_id == x:
                seen["charges"] = ledger(c, x)[0]
                with psycopg.connect(migrated_db_url, autocommit=True) as admin:
                    # with a timeout: wait until the backend has really exited
                    admin.execute("SELECT pg_terminate_backend(%s, 5000)", (c.info.backend_pid,))
            return real(c, run_id, account_id, status, **kw)  # x: fails, the session is gone

        monkeypatch.setattr(billing, "record_outcome", dies_before_the_row)
        clock.set(at(DEC1, 0, 30))
        with pytest.raises(process.RunAborted) as aborted:
            bill_date(conn, DEC1, clock=clock, tz=TZ, trigger="scheduled")
        monkeypatch.setattr(billing, "record_outcome", real)
        seen["marked"] = aborted.value.marked
        with psycopg.connect(migrated_db_url, autocommit=True) as admin:
            seen["after"] = (ledger(admin, x), outcome(admin, DEC1, x))
            seen["status"] = billing.get_run(admin, DEC1)["status"]
            seen["locks"] = admin.execute(  # this database only: pg_locks is cluster-wide
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database ="
                " (SELECT oid FROM pg_database WHERE datname = current_database())"
            ).fetchone()[0]

        second = worker_db.connect(migrated_db_url)
        clock.set(at(DEC1, 3, 1))
        seen["takeover"] = bill_date(second, DEC1, clock=clock, tz=TZ, trigger="scheduled")
        return second

    try:
        s = scenario(api, worker, broken=False, first_run=dies_then_taken_over)
        x = s["ids"]["x"]
        assert len(seen["charges"]) == 1  # the charge existed inside the transaction
        assert seen["after"] == (([], []), None)  # ... and went with it: no charge, no row
        # a dead worker cannot mark its run: it stays running, its lock died with it
        assert (seen["marked"], seen["status"], seen["locks"]) == (False, "running", 0)
        assert (seen["takeover"]["status"], seen["takeover"]["attempt"]) == ("completed", 2)
        assert ledger(second, x) == control  # billed exactly once, as in the control
    finally:
        if second is not None:
            second.close()


# --- "completed" means every member is ok ---

NOW = datetime(2026, 2, 1, 0, 45, tzinfo=MSK)


@pytest.fixture
def members(db_conn):
    """Two member accounts (opened 2026-01-01) and a running run of 2026-02-01."""
    first, second = account(db_conn, name="First"), account(db_conn, name="Second")
    run = billing.insert_run(db_conn, date(2026, 2, 1), now=NOW, trigger="scheduled")
    return run, first["id"], second["id"]


@pytest.mark.capability("worker.completedAllOk")
def test_completed_when_every_member_is_ok(db_conn, members):
    run, first, second = members
    for acc in (first, second):
        billing.record_outcome(db_conn, run["id"], acc, "ok", now=NOW)
    assert billing.finish_run(db_conn, run["id"], now=NOW)["status"] == "completed"


@pytest.mark.capability("worker.completedAllOk")
@pytest.mark.parametrize("gap", ["failed", "blocked", "missing"])
def test_not_completed_while_a_member_is_not_ok(db_conn, members, gap):
    run, first, second = members
    billing.record_outcome(db_conn, run["id"], first, "ok", now=NOW)
    if gap == "failed":
        billing.record_outcome(db_conn, run["id"], second, "failed", now=NOW, error="boom")
    elif gap == "blocked":
        earlier = billing.insert_run(db_conn, date(2026, 1, 31), now=NOW, trigger="scheduled")
        billing.record_outcome(
            db_conn, run["id"], second, "blocked", now=NOW, waits_for=earlier["id"]
        )
    with rejected(db_conn, billing.RunNotComplete):
        billing.finish_run(db_conn, run["id"], now=NOW)
    assert billing.get_run(db_conn, date(2026, 2, 1))["status"] == "running"


@pytest.mark.capability("worker.completedAllOk")
def test_an_account_opened_at_the_run_dates_midnight_is_not_a_member(db_conn, members):
    run, first, second = members
    late = account(db_conn, name="Late")
    db_conn.execute(
        "UPDATE billing_account SET opened_at = %s WHERE id = %s",
        (datetime(2026, 2, 1, 0, 0, tzinfo=MSK), late["id"]),  # 00:00 of the run date
    )
    for acc in (first, second):
        billing.record_outcome(db_conn, run["id"], acc, "ok", now=NOW)
    assert billing.finish_run(db_conn, run["id"], now=NOW)["status"] == "completed"


@pytest.mark.capability("worker.completedAllOk")
def test_a_run_with_a_failed_or_blocked_account_closes_failed(api, worker):
    scenario(api, worker, broken=True)
    for day in (DEC1, DEC2, DEC3):
        run = billing.get_run(worker, day)
        assert (run["status"], run["failure_kind"]) == ("failed", "accounts")
        assert run["finished_at"] is not None
    assert billing.get_run(worker, DEC2)["error"] == "1 of 3 accounts not billed (1 blocked)"


# --- the driver ---


def test_the_driver_processes_one_account_per_step(api, worker):
    api.clock.set(timeline.NOW)
    offering = apidata.sellable_offering(api, value=300)
    api.clock.set(at(NOV20, 9))
    for _ in range(3):
        add(api, apidata.account(api), offering, DEC1, on=at(NOV20))
    api.clock.set(at(DEC1, 0, 30))
    started = start_run(worker, DEC1, now=api.clock.now(), trigger="scheduled")
    date_run = DateRun(worker, started.run, clock=api.clock, tz=TZ)
    assert date_run.step() is True
    assert len(billing.outcomes(worker, started.run["id"])) == 1
    date_run.run_all()
    assert len(billing.outcomes(worker, started.run["id"])) == 3
    assert date_run.step() is False
    assert date_run.close()["status"] == "completed"
