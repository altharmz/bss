"""Charge cadences are independent of monthly invoices; exact nets survive refunds."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.billing.core import charge_period, compute
from tests.billing_sim import OFFERING_B, Ledger, account, price, product, rng

pytestmark = pytest.mark.smoke


def recurring(unit, length=1, amount="100.00", **over):
    return replace(price(amount, **over), recurring_period=unit, recurring_period_length=length)


@pytest.mark.parametrize(
    "unit,length,end",
    [
        ("daily", 1, date(2026, 10, 16)),
        ("daily", 3, date(2026, 10, 18)),
        ("weekly", 1, date(2026, 10, 22)),
        ("weekly", 2, date(2026, 10, 29)),
        ("monthly", 3, date(2027, 1, 15)),
        ("yearly", 1, date(2027, 10, 15)),
        ("yearly", 2, date(2028, 10, 15)),
    ],
)
def test_full_first_interval_and_renewal(unit, length, end):
    start = date(2026, 10, 15)
    book = Ledger(account(), (recurring(unit, length),))
    p = product(start)
    [first] = book.run([p], start).abrs
    assert first.period == rng(start, end)
    assert first.amount == Decimal("100.00")
    assert book.run([p], start).is_empty
    assert book.run([p], end - timedelta(days=1)).abrs == ()
    [second] = book.run([p], end).abrs
    assert second.period.start == end
    assert second.amount == first.amount
    assert book.run([p], end).is_empty


def test_anniversaries_do_not_drift_after_a_clamped_month_end():
    p = recurring("monthly", 2)
    anchor = date(2026, 12, 31)
    assert charge_period(date(2027, 2, 28), anchor, p, 1) == rng(
        date(2027, 2, 28), date(2027, 4, 30)
    )
    assert charge_period(date(2027, 8, 31), anchor, p, 1).start == date(2027, 8, 31)
    yearly = recurring("yearly")
    anchor = date(2024, 2, 29)
    assert charge_period(date(2025, 2, 28), anchor, yearly, 1).start == date(2025, 2, 28)
    assert charge_period(date(2027, 2, 28), anchor, yearly, 1).end == date(2028, 2, 29)


@pytest.mark.parametrize(
    "unit,length", [("daily", 5), ("weekly", 2), ("monthly", 3), ("yearly", 1)]
)
def test_partial_termination_refund_is_exact_and_idempotent(unit, length):
    start, end = date(2026, 10, 15), date(2026, 10, 17)
    p = recurring(unit, length, "3.25", taxes=(("VAT", "20"),))
    book = Ledger(account(), (p,))
    [charge] = book.run([product(start)], start).abrs
    [refund] = book.run([product(start, end)], end).abrs
    days = (charge.period.end - start).days
    expected = (Decimal("3.25") * 2 / days).quantize(Decimal("0.01"))
    assert charge.amount + refund.amount == expected
    assert charge.taxes[0].amount + refund.taxes[0].amount == (expected * Decimal("0.20")).quantize(
        Decimal("0.01")
    )
    assert refund.period == rng(end, charge.period.end)
    assert book.run([product(start, end)], end).is_empty


def test_switch_from_yearly_to_weekly_refunds_old_period_and_starts_new_schedule():
    a = recurring("yearly", amount="365.00")
    b = recurring("weekly", amount="7.00", offering=OFFERING_B)
    start, change = date(2026, 10, 15), date(2026, 10, 25)
    book = Ledger(account(), (a, b))
    book.run([product(start)], start)
    p = product(start, changes=((change, OFFERING_B),))
    result = book.run([p], change)
    assert [(a.kind, a.amount) for a in result.abrs] == [
        ("refund", Decimal("-355.00")),
        ("recurring", Decimal("7.00")),
    ]
    assert result.abrs[1].period == rng(change, date(2026, 11, 1))
    assert book.run([p], change).is_empty
    [renewal] = book.run([p], date(2026, 11, 1)).abrs
    assert renewal.amount == Decimal("7.00")


@given(
    unit=st.sampled_from(["daily", "weekly", "monthly", "yearly"]),
    length=st.integers(2, 5),
    days=st.integers(1, 90),
)
def test_daily_and_catchup_have_equal_charge_intervals(unit, length, days):
    p = product(date(2026, 10, 15))
    prices = (recurring(unit, length),)
    daily, catchup = Ledger(account(), prices), Ledger(account(), prices)
    for i in range(days + 1):
        daily.run([p], p.start_date + timedelta(days=i))
    catchup.run([p], p.start_date + timedelta(days=days))
    assert [(a.period, a.amount) for a in daily.charges()] == [
        (a.period, a.amount) for a in catchup.charges()
    ]


def test_weekly_intervals_count_business_dates_across_dst():
    p = product(date(2026, 3, 25))
    state = Ledger(account(), (recurring("weekly"),)).state([p])
    [charge] = compute(state, p.start_date, ZoneInfo("Europe/Berlin")).abrs
    assert charge.period == rng(date(2026, 3, 25), date(2026, 4, 1))
    assert charge.amount == Decimal("100.00")


def test_monthly_invoices_collect_weekly_charges_without_resetting_the_schedule():
    start = date(2026, 10, 15)
    p = product(start)
    book = Ledger(account(), (recurring("weekly", amount="7.00"),))
    for day in (start, date(2026, 10, 22), date(2026, 10, 29)):
        assert book.run([p], day).bills == ()
    result = book.run([p], date(2026, 11, 1))
    assert result.abrs == ()
    [bill] = result.bills
    assert bill.period == rng(date(2026, 11, 1), date(2026, 12, 1))
    assert bill.amount_due == Decimal("21.00")
    [next_charge] = book.run([p], date(2026, 11, 5)).abrs
    assert next_charge.period.start == date(2026, 11, 5)


@pytest.mark.parametrize(
    "before,after",
    [
        ("monthly", "weekly"),
        ("weekly", "monthly"),
        ("yearly", "daily"),
        ("daily", "yearly"),
        ("weekly", "yearly"),
    ],
)
def test_replay_across_a_cadence_change_and_later_termination_has_the_same_net(before, after):
    start = date(2026, 10, 15)
    change, ends = start + timedelta(days=3), start + timedelta(days=8)
    prices = (
        recurring(before, amount="100.00"),
        recurring(after, amount="70.00", offering=OFFERING_B),
    )
    daily, catchup = Ledger(account(), prices), Ledger(account(), prices)
    for i in range(10):
        day = start + timedelta(days=i)
        p = product(
            start,
            ends if day >= ends else None,
            changes=((change, OFFERING_B),) if day >= change else (),
        )
        daily.run([p], day)
    p = product(start, ends, changes=((change, OFFERING_B),))
    catchup.run([p], start + timedelta(days=9))

    def net(book):
        return sum((a.amount for a in book.abrs), Decimal("0.00"))

    assert net(daily) == net(catchup)
    assert daily.run([p], start + timedelta(days=9)).is_empty
