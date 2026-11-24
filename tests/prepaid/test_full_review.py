"""The daily full review of the prepaid accounts (#404): on the first wake-up of a business
date the lifecycle, entitlement and low-balance/grace passes find the accounts with
something to do in a few set-based statements and evaluate only those, under their locks.
The rules are the per-account ones; tests/prepaid/test_prepaid_lifecycle.py, test_notifications.py
and test_prepaid_entitlement.py take every transition through full reviews."""
# ruff: noqa: F811

import itertools
from datetime import date, timedelta

from psycopg.types.range import Range

from app.billing import entitlement
from app.repository import lifecycle as lifecycle_repo
from app.worker.lifecycle import process_lifecycle
from app.worker.notify import process_notifications
from app.worker.prepaid import observe_entitlements
from tests import apidata
from tests.builders.billing.settlement_db import make_payment
from tests.builders.prepaid.prepaid_lifecycle import at, go, numbered  # noqa: F401 - fixture
from tests.builders.prepaid.prepaid_plans import build  # noqa: F401 - fixture
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import stub  # noqa: F401 - fixture
from tests.perf.guard import counting
from tests.timeline import TZ

ACCOUNT_LOCK = "SELECT * FROM billing_account WHERE id = %s FOR UPDATE"  # load_account_row
PASSES = (process_lifecycle, observe_entitlements, process_notifications)

TODAY = date(2026, 6, 10)
DAY = timedelta(days=1)
GRANTS = (
    None,
    (TODAY - 30 * DAY, TODAY),  # ended yesterday
    (TODAY - 10 * DAY, TODAY + 20 * DAY),  # current
    (TODAY, TODAY + 30 * DAY),  # starts today
    (TODAY + DAY, TODAY + 31 * DAY),  # starts later
)


def test_the_sql_status_is_the_effective_state(db_conn):
    """`EFFECTIVE_STATUS` agrees with `effective_state` on every combination that decides it."""
    query = (
        "SELECT " + entitlement.EFFECTIVE_STATUS + " FROM (SELECT %(status)s::text AS status,"  # noqa: S608 - fixed SQL
        " %(termination)s::date AS termination_date, %(manual)s::bool AS manually_suspended,"
        " %(grantless)s::bool AS grantless) p, (SELECT %(state)s::text AS state) a,"
        " (SELECT %(period)s::daterange AS period) g"
    )
    combinations = itertools.product(
        ("created", "pendingActive", "active", "suspended", "pendingTerminate"),
        (None, TODAY - DAY, TODAY, TODAY + DAY),
        (False, True),
        (False, True, None),
        ("Active", "Suspended", "Closed"),
        GRANTS,
    )
    checked = 0
    for status, termination, manual, grantless, state, grant in combinations:
        product = {
            "status": status,
            "termination_date": termination,
            "manually_suspended": manual,
            "lifecycle_suspended": False,
            "grantless": grantless,
        }
        period = None if grant is None else Range(*grant, "[)")
        expected = entitlement.effective_state(
            product, {"state": state}, None if grant is None else {"period": period}, TODAY
        )[0]
        params = {
            "status": status,
            "termination": termination,
            "manual": manual,
            "grantless": grantless,
            "state": state,
            "period": period,
            "today": TODAY,
        }
        assert db_conn.execute(query, params).fetchone()[0] == expected, params
        checked += 1
    assert checked == 5 * 4 * 2 * 3 * 3 * len(GRANTS)


def account_locks(api, db_conn, monkeypatch, day) -> list[int]:
    """The accounts each full pass locks and evaluates on `day`."""
    api.clock.set(at(day))
    locks = []
    for run in PASSES:
        with counting(monkeypatch) as seen:
            run(db_conn, clock=api.clock, tz=TZ)
        db_conn.commit()
        locks.append(seen.count(ACCOUNT_LOCK))
    return locks


def test_a_full_review_evaluates_only_the_accounts_with_something_to_do(
    api, db_conn, numbered, monkeypatch
):
    """A funded line whose low balance was told: no pass locks its account. The day its plan
    ends, each pass has something to do and evaluates it; the grace message, once told, is
    not looked for again. The lifecycle evaluates the line on the day of its suspension and
    of its deactivation, not in grace or while it waits suspended."""
    go(api, db_conn, numbered.due - 5 * DAY)  # the low-balance message is told
    assert account_locks(api, db_conn, monkeypatch, numbered.due - 5 * DAY) == [0, 0, 0]
    # the plan ended today: the status turns suspended, the grace message is due
    assert account_locks(api, db_conn, monkeypatch, numbered.due) == [0, 1, 1]
    assert account_locks(api, db_conn, monkeypatch, numbered.due) == [0, 0, 0]
    settings = lifecycle_repo.settings(db_conn)
    suspend_on = numbered.due + settings.grace_days * DAY
    assert account_locks(api, db_conn, monkeypatch, suspend_on - DAY) == [0, 0, 0]
    assert account_locks(api, db_conn, monkeypatch, suspend_on)[0] == 1
    go(api, db_conn, suspend_on)  # the suspension order completes
    assert row_suspended(db_conn, numbered.product)
    deactivate_on = suspend_on + settings.suspension_days * DAY
    assert account_locks(api, db_conn, monkeypatch, deactivate_on - DAY) == [0, 0, 0]
    assert account_locks(api, db_conn, monkeypatch, deactivate_on)[0] == 1


def row_suspended(db_conn, product_id) -> bool:
    return db_conn.execute(
        "SELECT lifecycle_suspended FROM product WHERE id = %s", (product_id,)
    ).fetchone()[0]


def test_a_full_review_evaluates_a_pay_as_you_go_account_when_its_line_is_due(
    api, db_conn, build, monkeypatch
):
    """The zero-balance date set or cleared, the suspension and the resumption of a
    pay-as-you-go line are found by the full review; the days between are not."""
    world = build("2.40", fee=False)  # the one-time fee (2 + 20% tax) uses everything
    placed = apidata.order(api, world.acc, apidata.add_item(world.payg))
    tick(db_conn, api.clock.now())
    assert api.get(placed["href"]).json()["state"] == "completed"
    start = api.clock.now().astimezone(TZ).date()
    account = world.acc["id"]
    zero = "SELECT balance_zero_since FROM billing_account WHERE id = %s"
    # a zero balance without its date (no change marked it): the full review sets it
    db_conn.execute(
        "UPDATE billing_account SET balance_zero_since = NULL WHERE id = %s", (account,)
    )
    db_conn.commit()
    assert lifecycle_locks(api, db_conn, monkeypatch, start) == 1
    assert db_conn.execute(zero, (account,)).fetchone() == (start,)
    settings = lifecycle_repo.settings(db_conn)
    suspend_on = start + (settings.payg_zero_balance_days + settings.grace_days) * DAY
    assert lifecycle_locks(api, db_conn, monkeypatch, suspend_on - DAY) == 0
    assert lifecycle_locks(api, db_conn, monkeypatch, suspend_on) == 1
    go(api, db_conn, suspend_on)  # the suspension order completes
    assert lifecycle_locks(api, db_conn, monkeypatch, suspend_on + DAY) == 0
    make_payment(db_conn, dict(world.acc, currency="USD"), "5")  # funded again
    db_conn.commit()
    assert lifecycle_locks(api, db_conn, monkeypatch, suspend_on + DAY) == 1
    assert db_conn.execute(zero, (account,)).fetchone() == (None,)


def lifecycle_locks(api, db_conn, monkeypatch, day) -> int:
    api.clock.set(at(day))
    with counting(monkeypatch) as seen:
        process_lifecycle(db_conn, clock=api.clock, tz=TZ)
    db_conn.commit()
    return seen.count(ACCOUNT_LOCK)
