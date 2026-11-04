"""The schema itself rejects invalid data - independent of any application code."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg import errors
from psycopg.types.range import Range

from app.repository import billing, catalog, orders, parties, payments, products
from tests.factories import (
    NOW,
    T0,
    account,
    charge,
    days,
    offering,
    product,
    rejected,
    world,
)

pytestmark = pytest.mark.smoke


def _price(conn, offering_id, start, end, currency="USD"):
    return catalog.create_product_offering_price(
        conn,
        product_offering_id=offering_id,
        price_type="recurring",
        recurring_period="monthly",
        amount=Decimal("500.00"),
        currency=currency,
        valid_for=Range(start, end, "[)"),
    )


def test_overlapping_price_is_rejected_adjacent_is_accepted(db_conn):
    off = offering(db_conn)
    a, b, c = T0, T0.replace(month=3), T0.replace(month=6)
    _price(db_conn, off["id"], a, b)
    _price(db_conn, off["id"], b, c)  # [a,b) and [b,c) touch but do not overlap
    _price(db_conn, off["id"], a, b, currency="GBP")  # other currency: its own timeline
    with rejected(db_conn, errors.ExclusionViolation):
        _price(db_conn, off["id"], a.replace(month=2), None)


def test_recurring_price_requires_period(db_conn):
    off = offering(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        catalog.create_product_offering_price(
            db_conn,
            product_offering_id=off["id"],
            price_type="recurring",
            amount=Decimal("1.00"),
            currency="USD",
            valid_for=Range(T0, None, "[)"),
        )


def test_closed_upper_bound_is_rejected(db_conn):
    spec = catalog.create_product_specification(db_conn, name="x", lifecycle_status="Active")
    with rejected(db_conn, errors.CheckViolation):
        catalog.create_product_offering(
            db_conn,
            product_specification_id=spec["id"],
            name="x",
            lifecycle_status="Active",
            valid_for=Range(T0, NOW, "[]"),
        )


def test_duplicate_abr_is_rejected(db_conn):
    w = world(db_conn)
    charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    with rejected(db_conn, errors.UniqueViolation):
        charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))


def test_one_charge_per_period_and_offering_overlaps_are_allowed(db_conn):
    """Since 0027 (offering changes) a charge is refunded, never shortened, so the old
    offering's charge overlaps the new one's; since 0049 (mid-period prepaid change) they
    may share a first day. What stays unique is the product, offering and period."""
    w = world(db_conn)
    charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    charge(db_conn, w, days(date(2026, 1, 10), date(2026, 2, 1)))  # from a change day
    charge(db_conn, w, days(date(2026, 1, 10), date(2026, 1, 20)))  # same first day, other end
    with rejected(db_conn, errors.UniqueViolation):
        charge(db_conn, w, days(date(2026, 1, 10), date(2026, 1, 20)))


def _refund(conn, w, amount, refunds_rate_id, product_id=None):
    return billing.insert_abr(
        conn,
        product_id=product_id or w.product["id"],
        billing_account_id=w.account["id"],
        charge_type="refund",
        amount=Decimal(amount),
        currency="USD",
        period=days(date(2026, 1, 20), date(2026, 2, 1)),
        billing_run_id=w.run["id"],
        applied_at=w.run["started_at"],
        refunds_rate_id=refunds_rate_id,
    )


def test_refund_with_positive_amount_is_rejected(db_conn):
    w = world(db_conn)
    rate = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    with rejected(db_conn, errors.CheckViolation):
        _refund(db_conn, w, "10.00", rate["id"])


def test_refund_without_refunded_rate_is_rejected(db_conn):
    w = world(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        _refund(db_conn, w, "-10.00", None)


def test_second_refund_of_the_same_charge_is_rejected(db_conn):
    w = world(db_conn)
    rate = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    _refund(db_conn, w, "-10.00", rate["id"])
    with rejected(db_conn, errors.UniqueViolation):
        _refund(db_conn, w, "-5.00", rate["id"])


def test_refund_of_another_products_charge_is_rejected(db_conn):
    w = world(db_conn)
    other = product(db_conn, w.account["id"], w.offering["id"], date(2026, 1, 1))
    jan = days(date(2026, 1, 1), date(2026, 2, 1))
    other_rate = charge(db_conn, w, jan, product_id=other["id"])
    with rejected(db_conn, errors.ForeignKeyViolation):
        _refund(db_conn, w, "-10.00", other_rate["id"])


def test_abr_on_a_bill_of_another_account_is_rejected(db_conn):
    w = world(db_conn)
    stranger = account(db_conn, name="Petrov")
    bill = billing.insert_bill(
        db_conn,
        billing_account_id=stranger["id"],
        currency="USD",
        bill_period=days(date(2026, 1, 1), date(2026, 2, 1)),
        bill_date=date(2026, 2, 1),
        amount_due=Decimal("0.00"),
        billing_run_id=w.run["id"],
        payment_due_date=date(2026, 3, 3),
    )
    with rejected(db_conn, errors.ForeignKeyViolation):
        billing.insert_abr(
            db_conn,
            product_id=w.product["id"],
            billing_account_id=w.account["id"],
            charge_type="recurring",
            amount=Decimal("1.00"),
            currency="USD",
            period=days(date(2026, 1, 1), date(2026, 2, 1)),
            billing_run_id=w.run["id"],
            applied_at=w.run["started_at"],
            customer_bill_id=bill["id"],
        )


def test_overlapping_bill_periods_are_rejected(db_conn):
    w = world(db_conn)

    def bill(start, end):
        return billing.insert_bill(
            db_conn,
            billing_account_id=w.account["id"],
            currency="USD",
            bill_period=days(start, end),
            bill_date=end,
            amount_due=Decimal("0.00"),
            billing_run_id=w.run["id"],
            payment_due_date=end + timedelta(days=30),
        )

    bill(date(2026, 1, 1), date(2026, 2, 1))
    bill(date(2026, 2, 1), date(2026, 3, 1))
    with rejected(db_conn, errors.ExclusionViolation):
        bill(date(2026, 1, 15), date(2026, 2, 15))


def test_currency_must_match_billing_account(db_conn):
    acc = account(db_conn, currency="USD")
    with rejected(db_conn, errors.ForeignKeyViolation):
        payments.insert_payment(
            db_conn,
            billing_account_id=acc["id"],
            amount=Decimal("1.00"),
            currency="GBP",
            payment_method="bankTransfer",
            received_at=NOW,
        )


def test_delete_item_without_product_is_rejected(db_conn):
    acc = account(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        orders.create_order(
            db_conn,
            billing_account_id=acc["id"],
            order_date=NOW,
            items=[orders.NewOrderItem(item_id="1", action="delete")],
        )


def test_a_modify_item_names_its_product_and_offering_unless_rejected(db_conn):
    w = world(db_conn)

    def modify(state: str, offering_id) -> dict:
        item = orders.NewOrderItem(
            item_id="1",
            action="modify",
            product_id=w.product["id"],
            product_offering_id=offering_id,
            state=state,
        )
        return orders.create_order(
            db_conn, billing_account_id=w.account["id"], order_date=NOW, items=[item]
        )

    assert modify("acknowledged", w.offering["id"])["items"][0]["action"] == "modify"
    assert modify("rejected", None)["items"][0]["state"] == "rejected"
    with rejected(db_conn, errors.CheckViolation):
        modify("acknowledged", None)


def test_second_open_delete_for_the_same_product_is_rejected(db_conn):
    w = world(db_conn)

    def delete_order():
        return orders.create_order(
            db_conn,
            billing_account_id=w.account["id"],
            order_date=NOW,
            items=[orders.NewOrderItem(item_id="1", action="delete", product_id=w.product["id"])],
        )

    first = delete_order()
    with rejected(db_conn, errors.UniqueViolation):
        delete_order()
    # once the first one reaches a final state, a new delete is allowed again
    orders.set_order_item_state(db_conn, first["items"][0]["id"], "failed")
    delete_order()


def test_bill_cycle_day_29_is_rejected(db_conn):
    acc = account(db_conn)
    parties.create_billing_account(
        db_conn,
        customer_id=acc["customer_id"],
        name="ok",
        currency="USD",
        bill_cycle_day=28,
        opened_at=acc["opened_at"],
    )
    with rejected(db_conn, errors.CheckViolation):
        parties.create_billing_account(
            db_conn,
            customer_id=acc["customer_id"],
            name="x",
            currency="USD",
            bill_cycle_day=29,
            opened_at=acc["opened_at"],
        )


def test_product_without_creating_order_item_is_rejected(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    with rejected(db_conn, errors.NotNullViolation):
        db_conn.execute(
            "INSERT INTO product (billing_account_id, product_offering_id, status, start_date)"
            " VALUES (%s, %s, 'active', %s)",
            (acc["id"], off["id"], date(2026, 1, 1)),
        )


def test_termination_before_start_is_rejected(db_conn):
    w = world(db_conn)
    with rejected(db_conn, errors.CheckViolation):
        products.terminate_product(
            db_conn,
            w.product["id"],
            termination_date=date(2025, 12, 31),
            terminated_by_item_id=w.product["created_by_item_id"],
        )


def test_bad_currency_code_is_rejected(db_conn):
    with rejected(db_conn, errors.CheckViolation):
        account(db_conn, currency="usd")
