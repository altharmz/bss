"""Postpaid ledger consistency and anniversary cutoffs on the migrated schema."""

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from psycopg.types.range import Range

from app.billing.model import AppliedTax, DateRange, InconsistentState
from app.repository import billing, products
from app.worker import db as worker_db
from app.worker.process import bill_date
from tests import apidata
from tests.builders.billing.billing_db import add, at, bill, delete
from tests.builders.catalog.api_catalog import make_offering, make_price, price_refs
from tests.builders.orders.api_order_modify import modify
from tests.timeline import TZ

START = date(2026, 10, 15)
CYCLE = date(2026, 11, 1)


def plan(api, unit="monthly", length=1, value=310, *, tax=None):
    price = make_price(
        api,
        recurringChargePeriodType=unit,
        recurringChargePeriodLength=length,
        price={"unit": "USD", "value": value},
        tax=[apidata.tax_item("VAT", 20)] if tax is None else tax,
    )
    return make_offering(api, productOfferingPrice=price_refs(price))


def ledger(api, conn, unit="monthly", length=1):
    offer = plan(api, unit, length)
    account = apidata.account(api)
    product = add(api, account, offer, START, on=at(START - timedelta(days=1)))
    bill(conn, account, START)
    return account, product


def rows(conn, account):
    return conn.execute(
        "SELECT id, product_id, period, amount, tax_amount, customer_bill_id, refunds_rate_id"
        " FROM applied_customer_billing_rate WHERE billing_account_id = %s ORDER BY id",
        (account["id"],),
    ).fetchall()


def assert_billing_refused(conn, account, day, match):
    conn.commit()
    rates = rows(conn, account)
    invoices = conn.execute("SELECT id, amount_due FROM customer_bill ORDER BY id").fetchall()
    with pytest.raises(InconsistentState, match=match):
        bill(conn, account, day)
    assert rows(conn, account) == rates
    assert (
        conn.execute("SELECT id, amount_due FROM customer_bill ORDER BY id").fetchall() == invoices
    )


def test_replay_refuses_a_charge_for_a_product_not_yet_visible(api, db_conn):
    account, product = ledger(api, db_conn)
    assert_billing_refused(
        db_conn, account, START - timedelta(days=1), f"product {product['id']} not in the account"
    )


@pytest.mark.parametrize(
    ("unit", "start", "end", "match"),
    [
        ("monthly", START, date(2026, 11, 2), "not inside one cycle period"),
        ("monthly", date(2026, 9, 1), date(2026, 9, 2), "it is not billed for"),
        ("weekly", START, CYCLE, "outside its charge period"),
        ("weekly", date(2026, 10, 16), date(2026, 10, 23), "outside its schedule"),
    ],
)
def test_a_malformed_charge_period_leaves_the_ledger_intact(api, db_conn, unit, start, end, match):
    account, product = ledger(api, db_conn, unit)
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET period = %s WHERE product_id = %s",
        (Range(start, end, "[)"), product["id"]),
    )
    assert_billing_refused(db_conn, account, START + timedelta(days=1), match)


def test_a_weekly_line_does_not_relax_the_monthly_lines_cycle_guard(api, db_conn):
    monthly_plan, weekly_plan = plan(api), plan(api, "weekly")
    account = apidata.account(api)
    placed_at = at(START - timedelta(days=1))
    monthly = add(api, account, monthly_plan, START, on=placed_at)
    add(api, account, weekly_plan, START, on=placed_at)
    bill(db_conn, account, START)
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET period = %s WHERE product_id = %s",
        (Range(START, date(2026, 11, 2), "[)"), monthly["id"]),
    )
    assert_billing_refused(
        db_conn, account, START + timedelta(days=1), "outside its monthly charge"
    )


@pytest.mark.parametrize("unit", ["monthly", "weekly"])
def test_a_second_charge_with_the_same_start_does_not_replace_the_first(api, db_conn, unit):
    account, product = ledger(api, db_conn, unit)
    [charge] = rows(db_conn, account)
    original = billing.get_abr(db_conn, charge[0])
    billing.insert_abr(
        db_conn,
        product_id=UUID(product["id"]),
        billing_account_id=UUID(account["id"]),
        charge_type="recurring",
        amount=D("10"),
        currency="USD",
        period=Range(START, START + timedelta(days=1), "[)"),
        billing_run_id=original["billing_run_id"],
        applied_at=original["applied_at"],
    )
    match = "ABRs of period" if unit == "monthly" else "duplicate charge starts"
    assert_billing_refused(db_conn, account, START, match)


@pytest.mark.parametrize(
    ("period", "match"),
    [
        (
            Range(date(2026, 11, 2), date(2026, 11, 3), "[)"),
            "does not belong to a charge of period",
        ),
        (Range(date(2026, 10, 14), CYCLE, "[)"), "does not belong to charge"),
        (None, "taxes .* do not match charge"),
    ],
)
def test_a_refund_must_match_its_charge_and_reverse_the_right_tax(api, db_conn, period, match):
    account, product = ledger(api, db_conn)
    delete(api, account, product, date(2026, 10, 20), on=at(date(2026, 10, 16)))
    day = date(2026, 10, 17)
    [refund] = bill(db_conn, account, day).abrs
    assert refund.amount == D("-120") and refund.taxes[0].amount == D("-24")
    if period is None:
        db_conn.execute(
            "UPDATE applied_customer_billing_rate SET tax_amount = -23,"
            " applied_tax = jsonb_set(applied_tax, '{0,taxAmount}', to_jsonb('-23.00'::text))"
            " WHERE charge_type = 'refund'"
        )
    else:
        db_conn.execute(
            "UPDATE applied_customer_billing_rate SET period = %s WHERE charge_type = 'refund'",
            (period,),
        )
    as_of = day if period is None else max(day, period.lower)
    assert_billing_refused(db_conn, account, as_of, match)


def test_replay_refuses_an_invoice_dated_after_the_run(api, db_conn):
    account, product = ledger(api, db_conn)
    delete(api, account, product, date(2026, 10, 20), on=at(date(2026, 10, 16)))
    bill(db_conn, account, date(2026, 10, 17))
    [invoice] = bill(db_conn, account, CYCLE).bills
    assert invoice.amount_due == D("60")
    assert_billing_refused(
        db_conn, account, CYCLE - timedelta(days=1), "bill .* dated 2026-11-01 > 2026-10-31"
    )


def test_replay_never_rolls_a_charge_that_missed_its_invoice_into_the_next_one(api, db_conn):
    account, _ = ledger(api, db_conn)
    bill(db_conn, account, CYCLE)
    [old, _] = db_conn.execute(
        "SELECT id FROM applied_customer_billing_rate ORDER BY lower(period)"
    ).fetchall()
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET customer_bill_id = NULL WHERE id = %s", old
    )
    assert_billing_refused(db_conn, account, date(2026, 12, 1), f"ABR {old[0]} .* missed the bill")


def test_anniversary_waits_for_the_effective_day_of_a_known_switch(api, db_conn):
    original, successor = plan(api, "weekly", value=35), plan(api, "weekly", value=70)
    account = apidata.account(api)
    product = add(api, account, original, START, on=at(START - timedelta(days=1)))
    change_day = START + timedelta(days=4)
    order = modify(api, account, product, successor, change_day, on=at(START))
    assert order["state"] == "completed", order
    [charge] = bill(db_conn, account, change_day - timedelta(days=1)).abrs
    assert charge.offering_id == UUID(original["id"])
    assert charge.amount == D("20") and charge.period == DateRange(START, change_day)
    [next_charge] = bill(db_conn, account, change_day).abrs
    assert next_charge.offering_id == UUID(successor["id"])
    assert next_charge.amount == D("70") and next_charge.period.start == change_day


def test_worker_replays_a_prorated_charge_with_fractional_jsonb_tax(api, migrated_db_url):
    original = plan(
        api,
        "weekly",
        value=7,
        tax=[apidata.tax_item("VAT", 20), apidata.tax_item("city", 2.3)],
    )
    successor = plan(api, "weekly", value=70)
    account = apidata.account(api)
    account_id = UUID(account["id"])
    product = add(api, account, original, START, on=at(START - timedelta(days=1)))
    change_day = START + timedelta(days=4)
    order = modify(api, account, product, successor, change_day, on=at(START))
    assert order["state"] == "completed", order

    first_day = change_day - timedelta(days=1)
    with worker_db.connect(migrated_db_url) as worker:
        api.clock.set(at(first_day))
        first = bill_date(worker, first_day, clock=api.clock, tz=TZ, trigger="scheduled")
        assert first["status"] == "completed", first
        [charge] = billing.load_abrs(worker, account_id)
        assert charge.amount == D("4.00") and charge.period == DateRange(START, change_day)

        api.clock.set(at(change_day))
        second = bill_date(worker, change_day, clock=api.clock, tz=TZ, trigger="scheduled")
        assert second["status"] == "completed", second
        [replayed, next_charge] = billing.load_abrs(worker, account_id)
        assert replayed == charge
        assert replayed.taxes == (
            AppliedTax("VAT", D("20"), D("0.80")),
            AppliedTax("city", D("2.3"), D("0.09")),
        )
        assert next_charge.offering_id == UUID(successor["id"])
        assert next_charge.amount == D("70.00") and next_charge.period.start == change_day


def test_a_cancelled_weekly_product_leaves_no_charge_or_bill(api, db_conn):
    offer = plan(api, "weekly")
    account = apidata.account(api)
    product = add(api, account, offer, START, on=at(START - timedelta(days=3)))
    delete(api, account, product, None, on=at(START - timedelta(days=2)))
    assert api.get(product["href"]).json()["status"] == "cancelled"
    assert bill(db_conn, account, CYCLE).is_empty
    assert rows(db_conn, account) == []


def test_anniversary_with_a_clamped_start_still_uses_the_original_day(api, db_conn):
    offer = plan(api, "monthly", length=2)
    account = apidata.account(api)
    anchor = date(2026, 10, 31)
    product = add(api, account, offer, anchor, on=at(anchor - timedelta(days=1)))
    row = products.get_product(db_conn, UUID(product["id"])) | {"currency": "USD"}

    def period(day):
        return billing.product_period(db_conn, row, day, 1)

    assert period(date(2026, 12, 30)) == DateRange(anchor, date(2026, 12, 31))
    assert period(date(2026, 12, 31)) == DateRange(date(2026, 12, 31), date(2027, 2, 28))
    assert period(date(2027, 2, 28)) == DateRange(date(2027, 2, 28), date(2027, 4, 30))
