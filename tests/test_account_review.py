"""Account review (#292): the worker's per-account prepaid passes look at every prepaid
account once per business date, and otherwise only at the accounts whose money or products
changed, marked by triggers (migration 0068)."""

from datetime import date, datetime, time, timedelta
from uuid import UUID

import pytest

from app.worker import loop
from tests import apidata, timeline
from tests.test_prepaid_orders import shop
from tests.test_prepaid_renewal_producer import pay, tick

DAY = date(2026, 6, 10)


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), timeline.TZ)


def marked(conn) -> set[str]:
    conn.commit()
    return {str(r[0]) for r in conn.execute("SELECT billing_account_id FROM account_review")}


def prepaid_account(api, conn, name):
    acc = apidata.account(api, name=name)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    conn.commit()
    return acc


@pytest.fixture
def seen(monkeypatch):
    """The `accounts` argument each pass got, per tick (None: every prepaid account)."""
    calls: list[dict[str, list[str] | None]] = []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            accounts = kwargs.get("accounts")
            calls[-1][name] = None if accounts is None else sorted(str(a) for a in accounts)
            return real(*args, **kwargs)

        monkeypatch.setattr(loop, name, wrapped)

    for name in ("process_lifecycle", "observe_entitlements", "process_notifications"):
        spy(name, getattr(loop, name))

    def run(conn, now):
        calls.append({})
        tick(conn, now)
        assert len({str(v) for v in calls[-1].values()}) == 1, calls[-1]  # all passes agree
        return calls[-1]["process_lifecycle"]

    return run


def test_a_change_marks_a_prepaid_account_and_never_a_postpaid_one(api, db_conn):
    _, prepaid = shop(api, db_conn, funds="0")
    postpaid = apidata.account(api, name="Postpaid")
    db_conn.execute("DELETE FROM account_review")  # the mode change above marked it
    db_conn.commit()
    pay(db_conn, UUID(postpaid["id"]), "5", at(DAY))
    assert marked(db_conn) == set()
    pay(db_conn, UUID(prepaid["id"]), "5", at(DAY))
    assert marked(db_conn) == {prepaid["id"]}
    version = db_conn.execute("SELECT version FROM account_review").fetchone()[0]
    pay(db_conn, UUID(prepaid["id"]), "5", at(DAY))  # a second change: same row, newer version
    assert db_conn.execute("SELECT count(*), max(version) FROM account_review").fetchone()[1] > (
        version
    )


def test_one_full_sweep_per_business_date_then_only_the_changed_accounts(api, db_conn, seen):
    _, first = shop(api, db_conn, funds="0")
    second = prepaid_account(api, db_conn, "Second")
    assert seen(db_conn, at(DAY)) is None  # the first tick of the date: all
    assert marked(db_conn) == set()  # what that sweep read is evaluated
    assert seen(db_conn, at(DAY, 10)) == []  # nothing changed: nobody
    pay(db_conn, UUID(second["id"]), "5", at(DAY, 10, 30))
    assert seen(db_conn, at(DAY, 11)) == [second["id"]]
    assert seen(db_conn, at(DAY, 12)) == []
    assert seen(db_conn, at(DAY + timedelta(days=1))) is None  # a new business date: all
    assert first["id"] != second["id"]


def test_a_settings_change_starts_a_full_sweep(api, db_conn, seen):
    shop(api, db_conn, funds="0")
    assert seen(db_conn, at(DAY)) is None
    assert seen(db_conn, at(DAY, 10)) == []
    response = api.put("/internal/v1/lifecycle/settings", {"graceDays": 5})
    assert response.status_code == 200, response.text
    assert seen(db_conn, at(DAY, 11)) is None
    assert seen(db_conn, at(DAY, 12)) == []


def test_a_change_during_a_tick_is_evaluated_on_the_next(api, db_conn, seen, monkeypatch):
    _, acc = shop(api, db_conn, funds="0")
    assert seen(db_conn, at(DAY)) is None
    real = loop.process_commands

    def paying(conn, **kwargs):  # a payment lands while the tick runs (between the passes)
        result = real(conn, **kwargs)
        pay(conn, UUID(acc["id"]), "5", at(DAY, 10))
        return result

    monkeypatch.setattr(loop, "process_commands", paying)
    assert seen(db_conn, at(DAY, 10)) == []  # read before the payment
    monkeypatch.setattr(loop, "process_commands", real)
    assert marked(db_conn) == {acc["id"]}  # kept: its version is newer than the one read
    assert seen(db_conn, at(DAY, 11)) == [acc["id"]]


def test_a_stopped_tick_keeps_the_marks_and_the_sweep_for_the_next(api, db_conn):
    _, acc = shop(api, db_conn, funds="0")
    loop.tick(
        db_conn,
        clock=api.clock.__class__(at(DAY)),
        tz=timeline.TZ,
        run_at=time(0, 30),
        checkpoint=lambda: True,
    )
    db_conn.commit()
    assert marked(db_conn) == {acc["id"]}
    assert db_conn.execute("SELECT count(*) FROM account_review_sweep").fetchone() == (0,)
