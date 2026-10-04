"""The billing core through the DB adapter against the real schema: orders placed through
the API (FixedClock), billing runs through `bill_account`, results read back through
TMF678 (contract-checked by the client).

Prices cannot start before the day after timeline.NOW (2026-10-01), so the brief's
October examples are played in December 2026, which also has 31 days - same numbers.
"""

import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import apply_result, bill_account, load_state
from app.billing.core import compute
from app.billing.model import BillingResult
from app.repository import billing
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, BILL, RATE
from tests.timeline import TZ, iso, midnight, z

D = Decimal


def at(day: date, hour: int = 10) -> datetime:
    return midnight(day) + timedelta(hours=hour)


def run_row(conn: psycopg.Connection, day: date) -> dict:
    """The billing run of `day`, started at 00:30 that day if it does not exist yet. Runs
    are left `running`: finishing them is stage 4's job."""
    run = billing.get_run(conn, day)
    if run is None:
        run = billing.insert_run(
            conn, day, now=midnight(day) + timedelta(minutes=30), trigger="scheduled"
        )
    return run


def bill(conn: psycopg.Connection, account: dict, day: date) -> BillingResult:
    result = bill_account(conn, UUID(account["id"]), run_row(conn, day))
    conn.commit()
    return result


def add(api, account: dict, offering: dict, start: date, *, on: datetime) -> dict:
    api.clock.set(on)
    placed = apidata.order(
        api, account, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def delete(api, account: dict, product: dict, effective: date | None, *, on: datetime) -> None:
    api.clock.set(on)
    over = {} if effective is None else {"requestedStartDate": iso(midnight(effective))}
    placed = apidata.order(api, account, apidata.delete_item(product["id"]), **over)
    assert placed["state"] == "completed", placed


def summary(result: BillingResult) -> list[tuple[str, Decimal, date, date]]:
    return [(a.kind, a.amount, a.period.start, a.period.end) for a in result.abrs]


def count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300)


# --- worked examples end to end ---


def test_examples_1_4_11_and_the_tmf678_view(api, db_conn, offering):
    account = apidata.account(api)  # cycle day 1, RUB
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))

    # example 1: the full period, charged in advance and billed the same day
    first = bill(db_conn, account, date(2026, 12, 1))
    assert summary(first) == [("recurring", D("300.00"), date(2026, 12, 1), date(2027, 1, 1))]
    assert [(b.bill_date, b.amount_due) for b in first.bills] == [(date(2026, 12, 1), D("300.00"))]

    # example 4: termination ordered Dec 19, effective Dec 20, seen by the run of Dec 20
    delete(api, account, product, date(2026, 12, 20), on=at(date(2026, 12, 19)))
    refund = bill(db_conn, account, date(2026, 12, 20))
    assert summary(refund) == [("refund", D("-116.13"), date(2026, 12, 20), date(2027, 1, 1))]
    assert refund.abrs[0].refunds_id == next(
        a.id for a in billing.load_abrs(db_conn, UUID(account["id"])) if a.kind == "recurring"
    )

    # example 11: the same run again writes nothing
    rows = count(db_conn, "applied_customer_billing_rate"), count(db_conn, "customer_bill")
    assert bill(db_conn, account, date(2026, 12, 20)).is_empty
    assert (
        count(db_conn, "applied_customer_billing_rate"),
        count(db_conn, "customer_bill"),
    ) == rows

    # the refund goes onto the next bill, which is negative
    january = bill(db_conn, account, date(2027, 1, 1))
    assert (january.abrs, [b.amount_due for b in january.bills]) == ((), [D("-116.13")])

    # TMF678, contract-checked by the client
    rates = decimals(api.get(f"{RATE}?billingAccount.id={account['id']}"))
    assert [
        (r["appliedBillingRateType"], r["taxExcludedAmount"]["value"], r["isBilled"]) for r in rates
    ] == [("appliedBillingCharge", D("300.00"), True), ("appliedBillingCredit", D("-116.13"), True)]
    assert rates[1]["periodCoverage"] == {
        "startDateTime": z(midnight(date(2026, 12, 20))),
        "endDateTime": z(midnight(date(2027, 1, 1))),
    }
    assert rates[0]["product"]["id"] == product["id"]
    bills = decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))
    assert [(b["billDate"], b["amountDue"]["value"]) for b in bills] == [
        (z(midnight(date(2026, 12, 1))), D("300.00")),
        (z(midnight(date(2027, 1, 1))), D("-116.13")),
    ]
    assert {r["bill"]["id"] for r in rates} == {b["id"] for b in bills}
    assert bills[1]["billingPeriod"] == {
        "startDateTime": z(midnight(date(2027, 1, 1))),
        "endDateTime": z(midnight(date(2027, 2, 1))),
    }
    # balance = payments - ABRs = 0 - 183.87
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    assert balance["amount"]["value"] == D("-183.87")


def test_example_8_catch_up_in_one_call(api, db_conn, offering):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 11, 1), on=at(date(2026, 10, 20)))
    result = bill(db_conn, account, date(2027, 1, 3))
    assert summary(result) == [
        ("recurring", D("300.00"), date(2026, 11, 1), date(2026, 12, 1)),
        ("recurring", D("300.00"), date(2026, 12, 1), date(2027, 1, 1)),
        ("recurring", D("300.00"), date(2027, 1, 1), date(2027, 2, 1)),
    ]
    assert result.bills == ()  # run date Jan 3: they wait for the Feb 1 bill
    # the Feb 1 bill takes those three and February's own charge, written by that run
    [feb] = bill(db_conn, account, date(2027, 2, 1)).bills
    assert feb.amount_due == D("1200.00")


# --- the database's constraints are in play ---


def test_a_stale_result_applied_twice_hits_the_exclusion_constraint(api, db_conn, offering):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run = run_row(db_conn, date(2026, 12, 1))
    state = load_state(db_conn, UUID(account["id"]), date(2026, 12, 1), TZ)
    result = compute(state, date(2026, 12, 1), TZ)
    with db_conn.transaction():
        apply_result(db_conn, state.account, run["id"], result)
    # what two concurrent runs computing from the same state would do without the lock
    with pytest.raises(psycopg.errors.ExclusionViolation), db_conn.transaction():
        apply_result(db_conn, state.account, run["id"], result)
    assert count(db_conn, "applied_customer_billing_rate") == 1


def test_two_concurrent_bill_account_calls_serialize_on_the_account(
    api, db_conn, offering, migrated_db_url
):
    """The first call holds the account row lock until it commits; the second waits,
    then sees the first one's ABRs and writes nothing - instead of computing from the
    same state and colliding on the exclusion constraint."""
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run = run_row(db_conn, date(2026, 12, 1))
    db_conn.commit()

    first = psycopg.connect(migrated_db_url)
    second = psycopg.connect(migrated_db_url)
    outcome: dict = {}

    def run_second() -> None:
        try:
            outcome["result"] = bill_account(second, UUID(account["id"]), run)
            second.commit()
        except Exception as error:  # reported by the assertion below
            outcome["error"] = error

    try:
        with first.transaction():
            assert len(bill_account(first, UUID(account["id"]), run).abrs) == 1
            thread = threading.Thread(target=run_second)
            thread.start()
            thread.join(timeout=2)
            assert thread.is_alive(), "the second call did not wait for the first"
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        first.close()
        second.close()

    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"].is_empty
    assert count(db_conn, "applied_customer_billing_rate") == 1


def test_a_run_that_is_not_running_writes_nothing(api, db_conn, offering):
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run = run_row(db_conn, date(2026, 12, 1))
    billing.fail_run(db_conn, run["id"], now=at(date(2026, 12, 1), 1), error="ended")
    with pytest.raises(LookupError, match="not running"):
        bill_account(db_conn, UUID(account["id"]), run)
    assert count(db_conn, "applied_customer_billing_rate") == 0


# --- visibility: run D sees orders completed before 00:00 of D ---


def test_an_order_placed_on_d_is_invisible_to_run_d_and_visible_to_run_d_plus_1(
    api, db_conn, offering
):
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 5), on=at(date(2026, 12, 5)))
    # the run of Dec 5 executes late in the evening, after the order: still invisible
    run = billing.insert_run(
        db_conn, date(2026, 12, 5), now=at(date(2026, 12, 5), 23), trigger="scheduled"
    )
    assert bill_account(db_conn, UUID(account["id"]), run).is_empty
    # Dec 6 charges from the start date: 300 x 27/31 = 261.29
    assert summary(bill(db_conn, account, date(2026, 12, 6))) == [
        ("recurring", D("261.29"), date(2026, 12, 5), date(2027, 1, 1))
    ]

    # the same for a termination: effective today, ordered today
    delete(api, account, product, None, on=at(date(2026, 12, 10)))
    assert bill(db_conn, account, date(2026, 12, 10)).is_empty
    # 300 x 5/31 = 48.39 expected; 48.39 - 261.29
    assert summary(bill(db_conn, account, date(2026, 12, 11))) == [
        ("refund", D("-212.90"), date(2026, 12, 10), date(2027, 1, 1))
    ]


def test_cancellation_before_the_start_is_never_charged(api, db_conn, offering):
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 20), on=at(date(2026, 12, 7)))
    delete(api, account, product, None, on=at(date(2026, 12, 8)))
    assert api.get(product["href"]).json()["status"] == "cancelled"
    for day in (date(2026, 12, 8), date(2026, 12, 20), date(2027, 1, 1)):
        assert bill(db_conn, account, day).is_empty


# --- replay after an outage equals daily runs ---


def abr_rows(conn: psycopg.Connection, account: dict, names: dict[str, str]) -> list[tuple]:
    """The account's ABRs with ids replaced by product names, so two accounts compare."""
    rows = conn.execute(
        """
        SELECT a.product_id, a.charge_type, a.amount, a.period, r.run_date, b.bill_date,
               c.period AS refunds_period
        FROM applied_customer_billing_rate a
        JOIN billing_run r ON r.id = a.billing_run_id
        LEFT JOIN customer_bill b ON b.id = a.customer_bill_id
        LEFT JOIN applied_customer_billing_rate c ON c.id = a.refunds_rate_id
        WHERE a.billing_account_id = %s
        """,
        (account["id"],),
    ).fetchall()
    return sorted(
        (names[str(pid)], kind, amount, period, run_date, bill_date, refunds)
        for pid, kind, amount, period, run_date, bill_date, refunds in rows
    )


def bill_rows(conn: psycopg.Connection, account: dict) -> list[tuple]:
    return conn.execute(
        "SELECT bill_date, bill_period, amount_due FROM customer_bill"
        " WHERE billing_account_id = %s ORDER BY bill_date",
        (account["id"],),
    ).fetchall()


def test_replay_after_an_outage_gives_the_same_rows_and_bills_as_daily_runs(api, db_conn, offering):
    """Two accounts receive the same orders at the same times. `daily` is billed every
    day at 00:30; `replayed` misses Dec 2..10 and has those dates replayed, in order, at
    00:30 on Dec 11 - after every order of the outage was placed."""
    daily, replayed = apidata.account(api), apidata.account(api)
    names: dict[str, str] = {}
    products: dict[tuple[str, str], dict] = {}

    def add_both(name: str, start: date, on: datetime) -> None:
        for acc in (daily, replayed):
            products[(acc["id"], name)] = add(api, acc, offering, start, on=on)
            names[products[(acc["id"], name)]["id"]] = name

    def delete_both(name: str, effective: date | None, on: datetime) -> None:
        for acc in (daily, replayed):
            delete(api, acc, products[(acc["id"], name)], effective, on=on)

    events = {
        date(2026, 11, 20): lambda: add_both("full", date(2026, 12, 1), at(date(2026, 11, 20))),
        # ordered on Dec 3 starting today: first charged by the run of Dec 4
        date(2026, 12, 3): lambda: add_both("same-day", date(2026, 12, 3), at(date(2026, 12, 3))),
        # termination known Dec 6, effective Dec 15: refunded by the run of Dec 7
        date(2026, 12, 6): lambda: delete_both("full", date(2026, 12, 15), at(date(2026, 12, 6))),
        date(2026, 12, 7): lambda: add_both("cancelled", date(2026, 12, 20), at(date(2026, 12, 7))),
        date(2026, 12, 8): lambda: delete_both("cancelled", None, at(date(2026, 12, 8))),
        # started and terminated on the same day: never charged
        date(2026, 12, 9): lambda: (
            add_both("zero", date(2026, 12, 9), at(date(2026, 12, 9))),
            delete_both("zero", None, at(date(2026, 12, 9), 15)),
        ),
    }
    outage = {date(2026, 12, 2) + timedelta(days=i) for i in range(9)}  # Dec 2..10

    events[date(2026, 11, 20)]()
    day = date(2026, 12, 1)
    while day <= date(2027, 1, 2):
        bill(db_conn, daily, day)
        if day == date(2026, 12, 11):
            for missed in sorted(outage):
                bill(db_conn, replayed, missed)
        if day not in outage:
            bill(db_conn, replayed, day)
        if day in events:
            events[day]()
        day += timedelta(days=1)

    assert abr_rows(db_conn, daily, names) == abr_rows(db_conn, replayed, names)
    assert bill_rows(db_conn, daily) == bill_rows(db_conn, replayed)

    # and the scenario exercised what it claims to
    rows = abr_rows(db_conn, daily, names)
    by_product = {
        name: [(r[1], r[2], r[4]) for r in rows if r[0] == name] for name in names.values()
    }
    assert by_product["full"] == [
        ("recurring", D("300.00"), date(2026, 12, 1)),
        ("refund", D("-164.52"), date(2026, 12, 7)),  # 300 x 14/31 = 135.48 kept
    ]
    assert by_product["same-day"] == [
        ("recurring", D("280.65"), date(2026, 12, 4)),  # 300 x 29/31
        ("recurring", D("300.00"), date(2027, 1, 1)),
    ]
    assert by_product["cancelled"] == by_product["zero"] == []
    assert [(r[0], r[2]) for r in bill_rows(db_conn, daily)] == [
        (date(2026, 12, 1), D("300.00")),
        (date(2027, 1, 1), D("416.13")),  # 280.65 - 164.52 + 300.00
    ]
