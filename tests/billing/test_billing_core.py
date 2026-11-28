"""The billing core against hand-checked examples (numbered as in the stage 3 brief)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.billing.core import cycle_period, prorate
from app.billing.model import DateRange, Exemption, InconsistentState, MissingPrice
from tests.billing_sim import (
    OFFERING,
    OFFERING_B,
    OFFERING_C,
    Ledger,
    account,
    midnight,
    price,
    product,
    rng,
)

pytestmark = pytest.mark.smoke

D = Decimal
P300 = price("300.00")


def ledger(*prices, cycle_day: int = 1) -> Ledger:
    return Ledger(account(cycle_day), prices or (P300,))


def amounts(result) -> list[tuple[str, Decimal, DateRange]]:
    return [(a.kind, a.amount, a.period) for a in result.abrs]


def test_date_ranges_reject_reversed_bounds_and_exclude_the_end_date():
    start, end = date(2026, 10, 1), date(2026, 11, 1)
    with pytest.raises(ValueError, match="range end 2026-10-01 before start 2026-11-01"):
        DateRange(end, start)
    october = DateRange(start, end)
    assert october.overlaps(DateRange(date(2026, 10, 31), date(2026, 11, 2)))
    assert not october.overlaps(DateRange(end, date(2026, 12, 1)))


# --- the eleven worked examples ---


def test_1_full_period_charged_in_advance():
    result = ledger().run([product(date(2026, 10, 1))], date(2026, 10, 1))
    assert amounts(result) == [
        ("recurring", D("300.00"), rng(date(2026, 10, 1), date(2026, 11, 1)))
    ]


def test_2_mid_period_start_is_prorated():
    result = ledger().run([product(date(2026, 10, 15))], date(2026, 10, 15))
    # 300 x 17/31 = 164.516...
    assert amounts(result) == [
        ("recurring", D("164.52"), rng(date(2026, 10, 15), date(2026, 11, 1)))
    ]


def test_3_half_even_rounding():
    result = ledger(price("3.25")).run([product(date(2026, 11, 16))], date(2026, 11, 16))
    # 3.25 x 15/30 = 1.625 exactly: half-even gives 1.62, half-up would give 1.63
    assert amounts(result) == [("recurring", D("1.62"), rng(date(2026, 11, 16), date(2026, 12, 1)))]


def test_4_termination_known_later_is_refunded():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    [charge] = book.charges()
    result = book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 20))
    # expected 300 x 19/31 = 183.87; refund = 183.87 - 300.00
    assert amounts(result) == [("refund", D("-116.13"), rng(date(2026, 10, 20), date(2026, 11, 1)))]
    assert result.abrs[0].refunds_id == charge.id
    assert charge.amount + result.abrs[0].amount == D("183.87")


def test_5_same_day_start_and_termination_is_never_charged():
    result = ledger().run([product(date(2026, 10, 15), date(2026, 10, 15))], date(2026, 10, 15))
    assert result.is_empty


def test_6_termination_on_the_period_boundary():
    book = ledger()
    october = book.run([product(date(2026, 10, 1), date(2026, 11, 1))], date(2026, 10, 1))
    assert amounts(october) == [
        ("recurring", D("300.00"), rng(date(2026, 10, 1), date(2026, 11, 1)))
    ]
    november = book.run([product(date(2026, 10, 1), date(2026, 11, 1))], date(2026, 11, 1))
    assert november.abrs == ()


def test_7_termination_known_before_charging_gives_one_reduced_charge():
    result = ledger().run([product(date(2026, 10, 1), date(2026, 10, 25))], date(2026, 10, 1))
    # 300 x 24/31 = 232.258...
    assert amounts(result) == [
        ("recurring", D("232.26"), rng(date(2026, 10, 1), date(2026, 10, 25)))
    ]


def test_8_catch_up_charges_every_missed_period():
    result = ledger().run([product(date(2026, 10, 1))], date(2026, 12, 3))
    assert amounts(result) == [
        ("recurring", D("300.00"), rng(date(2026, 10, 1), date(2026, 11, 1))),
        ("recurring", D("300.00"), rng(date(2026, 11, 1), date(2026, 12, 1))),
        ("recurring", D("300.00"), rng(date(2026, 12, 1), date(2027, 1, 1))),
    ]
    # run date Dec 3 is after every cycle day <= as_of: these wait for the Jan 1 bill
    assert result.bills == ()


def test_9_price_change_applies_from_the_next_period():
    a = price("300.00", date(2026, 1, 1), date(2026, 11, 1))
    b = price("330.00", date(2026, 11, 1))
    result = ledger(a, b).run([product(date(2026, 10, 1))], date(2026, 11, 1))
    assert [x.amount for x in result.abrs] == [D("300.00"), D("330.00")]


def test_9_price_boundary_is_midnight_in_billing_tz():
    # Nov 1 00:00 UTC is Nov 1 03:00 in Moscow: at Moscow midnight A is still valid
    utc_midnight = datetime(2026, 11, 1, tzinfo=UTC)
    a = price("300.00", date(2026, 1, 1), utc_midnight)
    b = price("330.00", utc_midnight)
    result = ledger(a, b).run([product(date(2026, 10, 1))], date(2026, 11, 1))
    assert [x.amount for x in result.abrs] == [D("300.00"), D("300.00")]


@pytest.mark.parametrize(("year", "days", "amount"), [(2027, 28, "289.29"), (2028, 29, "279.31")])
def test_10_period_length_is_the_denominator(year, days, amount):
    result = ledger(cycle_day=28).run([product(date(year, 3, 1))], date(year, 3, 1))
    [abr] = result.abrs
    assert cycle_period(date(year, 3, 1), 28).days == days
    # 300 x 27/28 = 289.2857..., 300 x 27/29 = 279.3103...
    assert (abr.amount, abr.period) == (D(amount), rng(date(year, 3, 1), date(year, 3, 28)))


def test_11_running_twice_emits_nothing():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    terminated = [product(date(2026, 10, 1), date(2026, 10, 20))]
    assert book.run(terminated, date(2026, 10, 20)).abrs
    assert book.run(terminated, date(2026, 10, 20)).is_empty


# --- charge trigger, statuses ---


def test_future_start_is_not_charged_before_its_first_billable_day():
    book = ledger()
    assert book.run([product(date(2026, 11, 5))], date(2026, 11, 1)).is_empty
    assert amounts(book.run([product(date(2026, 11, 5))], date(2026, 11, 5))) == [
        ("recurring", D("260.00"), rng(date(2026, 11, 5), date(2026, 12, 1)))
    ]


def test_product_cancelled_before_its_start_is_never_charged():
    cancelled = product(date(2026, 11, 5), date(2026, 11, 5), status="cancelled")
    assert ledger().run([cancelled], date(2026, 12, 3)).is_empty


@pytest.mark.parametrize("status", ["cancelled", "aborted"])
def test_cancelled_or_aborted_status_is_never_billable(status):
    assert ledger().run([product(date(2026, 10, 1), status=status)], date(2026, 10, 1)).is_empty


@pytest.mark.parametrize("status", ["created", "suspended", "pendingTerminate", "terminated"])
def test_other_statuses_are_billed_by_dates(status):
    result = ledger().run([product(date(2026, 10, 1), status=status)], date(2026, 10, 1))
    assert [a.amount for a in result.abrs] == [D("300.00")]


def test_charge_that_rounds_to_zero_is_still_written():
    result = ledger(price("0.01")).run([product(date(2026, 10, 31))], date(2026, 10, 31))
    assert amounts(result) == [("recurring", D("0.00"), rng(date(2026, 10, 31), date(2026, 11, 1)))]


def test_refund_that_rounds_to_zero_is_not_written():
    book = ledger(price("0.03"))
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    # 0.03 x 30/31 = 0.029 -> 0.03: nothing to refund
    assert book.run([product(date(2026, 10, 1), date(2026, 10, 31))], date(2026, 10, 2)).is_empty


def test_cancellation_after_a_charge_refunds_it_whole():
    book = ledger()
    book.run([product(date(2026, 10, 15))], date(2026, 10, 15))
    status_only = product(date(2026, 10, 15), date(2026, 10, 15), status="cancelled")
    result = book.run([status_only], date(2026, 10, 16))
    assert amounts(result) == [("refund", D("-164.52"), rng(date(2026, 10, 15), date(2026, 11, 1)))]


# --- prices ---


def test_missing_price_names_product_and_date():
    p = product(date(2026, 10, 1))
    with pytest.raises(MissingPrice) as err:
        ledger(price("300.00", date(2026, 10, 2))).run([p], date(2026, 10, 1))
    assert (err.value.product_id, err.value.day) == (p.id, date(2026, 10, 1))
    assert str(p.id) in str(err.value) and "2026-10-01" in str(err.value)


def test_price_in_another_currency_is_a_missing_price():
    with pytest.raises(MissingPrice, match="EUR"):
        ledger(price("300.00", currency="USD")).run([product(date(2026, 10, 1))], date(2026, 10, 1))


def test_price_in_the_account_currency_is_chosen():
    book = ledger(price("9.00", currency="USD"), price("300.00"))
    assert [a.amount for a in book.run([product(date(2026, 10, 1))], date(2026, 10, 1)).abrs] == [
        D("300.00")
    ]


def test_prorate_refuses_float():
    with pytest.raises(TypeError):
        prorate(300.0, 1, rng(date(2026, 10, 1), date(2026, 11, 1)))  # type: ignore[arg-type]


# --- bills ---


def test_bill_on_the_cycle_day_collects_the_charge_of_that_run():
    book = ledger()
    result = book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    [bill] = result.bills
    assert (bill.bill_date, bill.period, bill.amount_due) == (
        date(2026, 10, 1),
        rng(date(2026, 10, 1), date(2026, 11, 1)),
        D("300.00"),
    )
    assert book.abrs[0].bill_id == book.bills[0].id


def test_mid_cycle_start_goes_onto_the_next_bill():
    book = ledger()
    assert book.run([product(date(2026, 10, 15))], date(2026, 10, 15)).bills == ()
    [bill] = book.run([product(date(2026, 10, 15))], date(2026, 11, 1)).bills
    assert (bill.bill_date, bill.amount_due) == (date(2026, 11, 1), D("464.52"))  # 164.52 + 300


def test_bill_may_be_negative():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    terminated = [product(date(2026, 10, 1), date(2026, 10, 20))]
    book.run(terminated, date(2026, 10, 20))
    [bill] = book.run(terminated, date(2026, 11, 1)).bills
    assert bill.amount_due == D("-116.13")


def test_empty_bills_are_skipped_and_zero_bills_are_not():
    book = ledger(price("0.01"))
    assert book.run([], date(2026, 10, 1)).is_empty
    book.run([product(date(2026, 10, 31))], date(2026, 10, 31))
    [bill] = book.run([product(date(2026, 10, 31), date(2026, 11, 1))], date(2026, 11, 1)).bills
    assert bill.amount_due == D("0.00")


def test_missed_cycle_day_is_billed_with_what_was_known_by_then():
    book = ledger()
    book.run([product(date(2026, 10, 15))], date(2026, 10, 15))
    # Nov 1 and Dec 1 missed; at Dec 3 the Nov 1 bill takes the Oct 15 charge, the two
    # charges written at Dec 3 wait for Jan 1
    result = book.run([product(date(2026, 10, 15))], date(2026, 12, 3))
    [bill] = result.bills
    assert (bill.bill_date, bill.amount_due, len(result.abrs)) == (
        date(2026, 11, 1),
        D("164.52"),
        2,
    )


# --- inconsistent state is an error, never guessed around ---


def test_as_of_before_a_stored_run_raises():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 2))
    with pytest.raises(InconsistentState):
        book.compute([product(date(2026, 10, 1))], date(2026, 10, 1))


def test_stored_charge_that_no_longer_matches_its_price_raises():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    book.prices = (price("299.00"),)
    with pytest.raises(InconsistentState, match="does not match"):
        book.compute([product(date(2026, 10, 1))], date(2026, 10, 2))


def test_a_second_reduction_raises():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 2))
    with pytest.raises(InconsistentState, match="already refunded"):
        book.compute([product(date(2026, 10, 1), date(2026, 10, 10))], date(2026, 10, 3))


def test_expected_above_charged_raises():
    book = ledger()
    book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 1))
    with pytest.raises(InconsistentState, match="expected"):
        book.compute([product(date(2026, 10, 1))], date(2026, 10, 2))


# --- taxes (docs/decisions/billing-core.md#taxes-2026-10-18) ---

VAT20 = price("300.00", taxes=(("VAT", "20"),))


def taxes(abr) -> list[tuple[str, Decimal, Decimal]]:
    return [(t.category, t.rate, t.amount) for t in abr.taxes]


def test_a_charge_is_taxed_on_its_prorated_net_and_the_bill_includes_the_tax():
    result = ledger(VAT20).run([product(date(2026, 10, 15))], date(2026, 10, 15))
    [charge] = result.abrs
    # net 164.52; 164.52 x 20% = 32.904
    assert (charge.amount, taxes(charge)) == (D("164.52"), [("VAT", D("20"), D("32.90"))])
    [bill] = ledger(VAT20).run([product(date(2026, 10, 15))], date(2026, 11, 1)).bills
    assert bill.amount_due == D("164.52") + D("32.90") + D("300.00") + D("60.00")


def test_each_tax_item_is_a_line_rounded_once_half_even():
    two = price("3.25", taxes=(("VAT", "25"), ("city", "12.5")))
    [charge] = ledger(two).run([product(date(2026, 11, 16))], date(2026, 11, 16)).abrs
    # net 1.62: 0.405 -> 0.40 and 0.2025 -> 0.20 (half-even)
    assert taxes(charge) == [("VAT", D("25"), D("0.40")), ("city", D("12.5"), D("0.20"))]


def test_a_refund_takes_back_the_tax_line_by_line():
    book = ledger(VAT20)
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    [charge] = book.charges()
    [refund] = book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 20)).abrs
    # expected 183.87, its tax 36.774 -> 36.77; refund 36.77 - 60.00
    assert (refund.amount, taxes(refund)) == (D("-116.13"), [("VAT", D("20"), D("-23.23"))])
    assert charge.total + refund.total == D("183.87") + D("36.77")
    rerun = book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 21))
    assert rerun.is_empty


def test_an_exempt_tax_type_is_a_line_at_rate_zero_from_the_exemption_on():
    exempt = Exemption("VAT", midnight(date(2026, 11, 1)), None)
    book = Ledger(account(exemptions=(exempt,)), (VAT20,))
    october, november = (
        book.run([product(date(2026, 10, 1))], day)
        for day in (date(2026, 10, 1), date(2026, 11, 1))
    )
    assert taxes(october.abrs[0]) == [("VAT", D("20"), D("60.00"))]
    assert taxes(november.abrs[0]) == [("VAT", D("0"), D("0.00"))]
    other = Ledger(account(exemptions=(Exemption("city", None, None),)), (VAT20,))
    assert taxes(other.run([product(date(2026, 10, 1))], date(2026, 10, 1)).abrs[0]) == [
        ("VAT", D("20"), D("60.00"))
    ]


def test_an_exemption_granted_later_never_changes_a_charge_or_its_refund():
    book = ledger(VAT20)
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    book.account = account(exemptions=(Exemption("VAT", None, None),))
    [refund] = book.run([product(date(2026, 10, 1), date(2026, 10, 20))], date(2026, 10, 20)).abrs
    assert taxes(refund) == [("VAT", D("20"), D("-23.23"))]  # at the charge's rate


def test_a_stored_charge_whose_taxes_no_longer_match_its_price_raises():
    book = ledger()
    book.run([product(date(2026, 10, 1))], date(2026, 10, 1))
    # the price that made the charge, now with a tax item
    book.prices = (price("300.00", name="price:300.00:2020-01-01:EUR", taxes=(("VAT", "20"),)),)
    with pytest.raises(InconsistentState, match="taxes"):
        book.compute([product(date(2026, 10, 1))], date(2026, 10, 2))


# --- offering changes (docs/decisions/billing-core.md#offering-changes-2026-10-18) ---

OCT1, OCT10, OCT20, NOV1 = (
    date(2026, 10, 1),
    date(2026, 10, 10),
    date(2026, 10, 20),
    date(2026, 11, 1),
)
B620 = price("620.00", offering=OFFERING_B)  # 20.00 a day in October
C310 = price("310.00", offering=OFFERING_C)  # 10.00 a day in October


def changed(*changes, start=OCT1, termination=None):
    return product(start, termination, changes=changes)


def offerings(result) -> list:
    return [a.offering_id for a in result.abrs]


def test_a_change_seen_after_the_month_was_charged_refunds_the_old_and_charges_the_new():
    book = ledger(P300, B620)
    book.run([changed()], OCT1)
    to_b = changed((OCT10, OFFERING_B))
    early = book.run([to_b], date(2026, 10, 5))
    # A keeps 300 x 9/31 = 87.10; the new offering waits for its first day
    assert amounts(early) == [("refund", D("-212.90"), rng(OCT10, NOV1))]
    assert offerings(early) == [OFFERING]
    on_the_day = book.run([to_b], OCT10)
    assert amounts(on_the_day) == [("recurring", D("440.00"), rng(OCT10, NOV1))]  # 620 x 22/31
    assert offerings(on_the_day) == [OFFERING_B]
    assert book.run([to_b], date(2026, 10, 11)).is_empty


def test_a_change_first_seen_after_its_day_writes_refund_and_charge_in_one_run():
    book = ledger(P300, B620)
    book.run([changed()], OCT1)
    result = book.run([changed((OCT10, OFFERING_B))], date(2026, 10, 12))
    assert amounts(result) == [
        ("refund", D("-212.90"), rng(OCT10, NOV1)),
        ("recurring", D("440.00"), rng(OCT10, NOV1)),
    ]


def test_a_change_known_before_the_month_is_charged_gives_two_reduced_charges():
    book = ledger(P300, B620)
    to_b = changed((OCT10, OFFERING_B))
    assert amounts(book.run([to_b], OCT1)) == [("recurring", D("87.10"), rng(OCT1, OCT10))]
    assert amounts(book.run([to_b], OCT10)) == [("recurring", D("440.00"), rng(OCT10, NOV1))]
    assert book.refunds() == []


def test_a_change_on_the_cycle_day_is_a_plain_split_by_period():
    book = ledger(P300, B620)
    to_b = changed((NOV1, OFFERING_B))
    assert amounts(book.run([to_b], date(2026, 11, 3))) == [
        ("recurring", D("300.00"), rng(OCT1, NOV1)),
        ("recurring", D("620.00"), rng(NOV1, date(2026, 12, 1))),
    ]


def test_a_termination_after_a_change_cuts_the_new_offering_only():
    book = ledger(P300, B620)
    both = changed((OCT10, OFFERING_B), termination=OCT20)
    assert amounts(book.run([both], OCT10)) == [
        ("recurring", D("87.10"), rng(OCT1, OCT10)),
        ("recurring", D("200.00"), rng(OCT10, OCT20)),
    ]
    later = ledger(P300, B620)
    later.run([changed((OCT10, OFFERING_B))], OCT10)
    result = later.run([both], date(2026, 10, 15))
    assert amounts(result) == [("refund", D("-240.00"), rng(OCT20, NOV1))]
    assert offerings(result) == [OFFERING_B]


def test_two_changes_in_one_period_are_three_segments():
    book = ledger(P300, B620, C310)
    twice = changed((OCT10, OFFERING_B), (OCT20, OFFERING_C))
    result = book.run([twice], OCT20)
    assert amounts(result) == [
        ("recurring", D("87.10"), rng(OCT1, OCT10)),
        ("recurring", D("200.00"), rng(OCT10, OCT20)),
        ("recurring", D("120.00"), rng(OCT20, NOV1)),  # 310 x 12/31
    ]
    assert offerings(result) == [OFFERING, OFFERING_B, OFFERING_C]


def test_a_change_on_the_start_day_replaces_the_initial_offering():
    oct15 = date(2026, 10, 15)
    result = ledger(B620).run([changed((oct15, OFFERING_B), start=oct15)], oct15)
    assert amounts(result) == [("recurring", D("340.00"), rng(oct15, NOV1))]  # 620 x 17/31
    assert offerings(result) == [OFFERING_B]


def test_the_new_offering_is_priced_on_the_change_day():
    raised = price("930.00", date(2026, 10, 5), offering=OFFERING_B)
    book = ledger(P300, price("620.00", valid_to=date(2026, 10, 5), offering=OFFERING_B), raised)
    result = book.run([changed((OCT10, OFFERING_B))], OCT10)
    assert amounts(result)[1] == ("recurring", D("660.00"), rng(OCT10, NOV1))  # 930 x 22/31


def test_each_segment_is_rounded_once():
    # 0.02 x 9/31 = 0.0058 -> 0.01 and 0.02 x 22/31 = 0.0142 -> 0.01: two segments are two
    # charges, each one rounding of its own days, never a share of the month's rounding
    book = ledger(price("0.02"), price("0.02", offering=OFFERING_B))
    result = book.run([changed((OCT10, OFFERING_B))], OCT10)
    assert [a.amount for a in result.abrs] == [D("0.01"), D("0.01")]


def test_a_charge_of_another_offering_than_its_segment_raises():
    book = ledger(P300, B620)
    book.run([changed()], OCT1)
    with pytest.raises(InconsistentState, match="bills offering"):
        book.compute([changed((OCT1, OFFERING_B))], date(2026, 10, 2))


def test_a_charge_not_starting_on_a_segment_raises():
    book = ledger(P300, B620)
    book.run([changed((OCT10, OFFERING_B))], OCT10)
    with pytest.raises(InconsistentState, match="segment"):
        book.compute([changed()], date(2026, 10, 11))


def test_offering_changes_must_be_sorted_by_distinct_days():
    with pytest.raises(ValueError, match="sorted"):
        changed((OCT20, OFFERING_B), (OCT10, OFFERING_C))
    with pytest.raises(ValueError, match="sorted"):
        changed((OCT10, OFFERING_B), (OCT10, OFFERING_C))
