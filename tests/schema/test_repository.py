"""Repository functions and branches the API tests do not reach (#425: the other
happy-path tests were removed once coverage and testmon showed every line and branch they
reached is reached by an API or billing test).

- `price_at`: the half-open bounds of a price's validity.
- `list_active_products`: billing follows dates, not status.
- `list_abrs`: the period filter (half-open overlap); nothing else filters by period.
- `reopen_run` and `take_over_run`: the refusals of a run in the wrong state.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg.types.range import Range

from app.repository import billing, catalog, orders, products
from tests.factories import NOW, T0, account, charge, days, offering, product, world


def test_price_at(db_conn):
    off = offering(db_conn)
    switch = T0.replace(month=3)

    def price(amount, start, end):
        return catalog.create_product_offering_price(
            db_conn,
            product_offering_id=off["id"],
            price_type="recurring",
            recurring_period="monthly",
            amount=Decimal(amount),
            currency="USD",
            valid_for=Range(start, end, "[)"),
        )

    old, new = price("100.00", T0, switch), price("150.00", switch, None)

    def at(when):
        return catalog.price_at(
            db_conn,
            product_offering_id=off["id"],
            price_type="recurring",
            currency="USD",
            at=when,
        )

    assert at(T0)["id"] == old["id"]  # lower bound inclusive
    assert at(switch - timedelta(microseconds=1))["id"] == old["id"]
    assert at(switch)["id"] == new["id"]  # upper bound exclusive
    assert at(T0 - timedelta(days=1)) is None
    assert (
        catalog.price_at(
            db_conn, product_offering_id=off["id"], price_type="recurring", currency="GBP", at=T0
        )
        is None
    )


def test_list_active_products(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    other_acc = account(db_conn, name="Petrov")

    running = product(db_conn, acc["id"], off["id"], date(2026, 1, 1))
    # future-dated and not yet activated: billable from its start day anyway
    pending = product(db_conn, acc["id"], off["id"], date(2026, 1, 10))
    products.set_product_status(db_conn, pending["id"], "pendingActive")
    ended = product(db_conn, acc["id"], off["id"], date(2026, 1, 1))
    delete_item = orders.create_order(
        db_conn,
        billing_account_id=acc["id"],
        order_date=NOW,
        items=[orders.NewOrderItem(item_id="1", action="delete", product_id=ended["id"])],
    )["items"][0]
    products.terminate_product(
        db_conn,
        ended["id"],
        termination_date=date(2026, 1, 10),
        terminated_by_item_id=delete_item["id"],
    )
    never = product(db_conn, acc["id"], off["id"], date(2026, 1, 1))
    products.set_product_status(db_conn, never["id"], "cancelled")
    product(db_conn, other_acc["id"], off["id"], date(2026, 1, 1))

    def active_on(d):
        return {p["id"] for p in products.list_active_products(db_conn, acc["id"], d)}

    assert active_on(date(2026, 1, 9)) == {running["id"], ended["id"]}
    # termination_date is exclusive; start_date is inclusive; status does not matter
    assert active_on(date(2026, 1, 10)) == {running["id"], pending["id"]}
    assert active_on(date(2025, 12, 31)) == set()


def new_run(conn, day=date(2026, 2, 1), **over):
    return billing.insert_run(conn, day, now=over.get("now", NOW), trigger="scheduled")


@pytest.mark.parametrize("finish", [False, True], ids=["running", "completed"])
def test_reopen_run_only_reopens_a_failed_run(db_conn, finish):
    run = new_run(db_conn)
    if finish:
        billing.finish_run(db_conn, run["id"], now=NOW)
    with pytest.raises(LookupError):
        billing.reopen_run(db_conn, run["id"], now=NOW, trigger="retry")


def test_take_over_run_only_takes_over_a_running_run(db_conn):
    run = new_run(db_conn)
    billing.finish_run(db_conn, run["id"], now=NOW)
    with pytest.raises(LookupError):
        billing.take_over_run(db_conn, run["id"], now=NOW)


def test_list_abrs(db_conn):
    w = world(db_conn)
    jan = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    feb = charge(db_conn, w, days(date(2026, 2, 1), date(2026, 3, 1)))
    refund = billing.insert_abr(
        db_conn,
        product_id=w.product["id"],
        billing_account_id=w.account["id"],
        charge_type="refund",
        amount=Decimal("-50.00"),
        currency="USD",
        period=days(date(2026, 2, 15), date(2026, 3, 1)),
        billing_run_id=w.run["id"],
        applied_at=w.run["started_at"],
        refunds_rate_id=feb["id"],
    )
    all_ids = [a["id"] for a in billing.list_abrs(db_conn, product_id=w.product["id"])]
    assert all_ids == [jan["id"], feb["id"], refund["id"]]
    feb_ids = [
        a["id"]
        for a in billing.list_abrs(
            db_conn, product_id=w.product["id"], period=days(date(2026, 2, 1), date(2026, 3, 1))
        )
    ]
    assert feb_ids == [feb["id"], refund["id"]]  # [jan) ends where feb starts: no overlap
