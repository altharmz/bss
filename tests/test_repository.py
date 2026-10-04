"""One happy-path test per repository function."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from psycopg import errors
from psycopg.types.range import Range

from app.repository import billing, catalog, orders, parties, payments, products
from tests.factories import (
    MSK,
    NOW,
    T0,
    account,
    add_order,
    charge,
    days,
    offering,
    product,
    rejected,
    world,
)

# --- catalog ---


def test_create_and_get_product_specification(db_conn):
    spec = catalog.create_product_specification(
        db_conn, name="Fiber", lifecycle_status="In Design", description="FTTH"
    )
    assert catalog.get_product_specification(db_conn, spec["id"]) == spec
    assert spec["lifecycle_status"] == "In Design"


def test_create_and_get_product_offering(db_conn):
    off = offering(db_conn)
    got = catalog.get_product_offering(db_conn, off["id"])
    assert got == off
    assert got["is_sellable"] is True
    assert got["valid_for"] == Range(T0, None, "[)")


def test_create_and_get_product_offering_price(db_conn):
    off = offering(db_conn)
    price = catalog.create_product_offering_price(
        db_conn,
        product_offering_id=off["id"],
        price_type="recurring",
        recurring_period="monthly",
        amount=Decimal("499.90"),
        currency="RUB",
        valid_for=Range(T0, None, "[)"),
    )
    got = catalog.get_product_offering_price(db_conn, price["id"])
    assert got == price
    assert got["recurring_period_length"] == 1


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
            currency="RUB",
            valid_for=Range(start, end, "[)"),
        )

    old, new = price("100.00", T0, switch), price("150.00", switch, None)

    def at(when):
        return catalog.price_at(
            db_conn,
            product_offering_id=off["id"],
            price_type="recurring",
            currency="RUB",
            at=when,
        )

    assert at(T0)["id"] == old["id"]  # lower bound inclusive
    assert at(switch - timedelta(microseconds=1))["id"] == old["id"]
    assert at(switch)["id"] == new["id"]  # upper bound exclusive
    assert at(T0 - timedelta(days=1)) is None
    assert (
        catalog.price_at(
            db_conn, product_offering_id=off["id"], price_type="recurring", currency="USD", at=T0
        )
        is None
    )


# --- parties ---


def test_create_individual_and_get_party(db_conn):
    party = parties.create_individual(db_conn, given_name="Ivan", family_name="Ivanov")
    got = parties.get_party(db_conn, party["id"])
    assert got["party_type"] == "Individual"
    assert got["family_name"] == "Ivanov"


def test_create_organization(db_conn):
    org = parties.create_organization(db_conn, name="Romashka LLC")
    assert org["party_type"] == "Organization"
    assert org["organization_name"] == "Romashka LLC"


def test_create_and_get_customer(db_conn):
    party = parties.create_organization(db_conn, name="Romashka LLC")
    customer = parties.create_customer(db_conn, party_id=party["id"], name="Ivanov")
    assert parties.get_customer(db_conn, customer["id"]) == customer
    assert customer["status"] == "Created"


def test_create_and_get_billing_account(db_conn):
    acc = account(db_conn, currency="USD")
    got = parties.get_billing_account(db_conn, acc["id"])
    assert got == acc
    assert (got["currency"], got["state"], got["bill_cycle_day"]) == ("USD", "Active", 1)


# --- orders ---


def test_create_and_get_order(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    order = orders.create_order(
        db_conn,
        billing_account_id=acc["id"],
        order_date=NOW,
        requested_start_date=date(2026, 2, 1),
        items=[
            orders.NewOrderItem(item_id="1", action="add", product_offering_id=off["id"]),
            orders.NewOrderItem(item_id="2", action="add", product_offering_id=off["id"]),
        ],
    )
    got = orders.get_order(db_conn, order["id"])
    assert got["state"] == "acknowledged"
    assert got["order_date"] == NOW
    assert [i["item_id"] for i in got["items"]] == ["1", "2"]
    assert got["items"] == order["items"]


def test_set_order_state(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    order = add_order(db_conn, acc["id"], off["id"])
    done_at = NOW + timedelta(hours=1)
    orders.set_order_state(db_conn, order["id"], "inProgress")
    updated = orders.set_order_state(db_conn, order["id"], "completed", completion_date=done_at)
    assert updated["state"] == "completed"
    assert updated["completion_date"] == done_at


def test_set_order_item_state(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    item = add_order(db_conn, acc["id"], off["id"])["items"][0]
    assert orders.set_order_item_state(db_conn, item["id"], "completed")["state"] == "completed"


# --- products ---


def test_create_and_get_product(db_conn):
    off = offering(db_conn)
    acc = account(db_conn)
    prod = product(db_conn, acc["id"], off["id"], date(2026, 2, 1))
    got = products.get_product(db_conn, prod["id"])
    assert got == prod
    assert (got["status"], got["start_date"], got["termination_date"]) == (
        "active",
        date(2026, 2, 1),
        None,
    )


def test_set_product_status(db_conn):
    w = world(db_conn)
    assert products.set_product_status(db_conn, w.product["id"], "suspended")["status"] == (
        "suspended"
    )


def test_terminate_product(db_conn):
    w = world(db_conn)
    delete = orders.create_order(
        db_conn,
        billing_account_id=w.account["id"],
        order_date=NOW,
        items=[orders.NewOrderItem(item_id="1", action="delete", product_id=w.product["id"])],
    )["items"][0]
    done = products.terminate_product(
        db_conn,
        w.product["id"],
        termination_date=date(2026, 1, 20),
        terminated_by_item_id=delete["id"],
    )
    assert done["status"] == "terminated"
    assert done["termination_date"] == date(2026, 1, 20)
    assert done["terminated_by_item_id"] == delete["id"]


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


# --- billing ---


def new_run(conn, day=date(2026, 2, 1), **over):
    return billing.insert_run(conn, day, now=over.get("now", NOW), trigger="scheduled")


def test_insert_run(db_conn):
    run = billing.insert_run(db_conn, date(2026, 2, 1), now=NOW, trigger="catchUp")
    assert run["status"] == "running"
    assert run["started_at"] == NOW
    assert run["finished_at"] is None
    assert (run["trigger"], run["attempt"], run["failure_kind"]) == ("catchUp", 1, None)


def test_insert_run_is_one_per_date(db_conn):
    new_run(db_conn)
    with rejected(db_conn, errors.UniqueViolation):
        new_run(db_conn)


def test_lock_run(db_conn):
    assert billing.lock_run(db_conn, date(2026, 2, 1)) is None
    run = new_run(db_conn)
    assert billing.lock_run(db_conn, date(2026, 2, 1)) == run


def test_reopen_run_reuses_the_failed_row(db_conn):
    first = new_run(db_conn)
    billing.fail_run(db_conn, first["id"], now=NOW, error="boom")
    retry = billing.reopen_run(db_conn, first["id"], now=NOW + timedelta(hours=1), trigger="retry")
    assert retry["id"] == first["id"]
    assert (retry["status"], retry["error"], retry["finished_at"]) == ("running", None, None)
    assert retry["failure_kind"] is None  # the CHECK of 0004: only a failed run has one
    assert (retry["attempt"], retry["trigger"]) == (2, "retry")
    assert retry["started_at"] == NOW + timedelta(hours=1)


@pytest.mark.parametrize("finish", [False, True], ids=["running", "completed"])
def test_reopen_run_only_reopens_a_failed_run(db_conn, finish):
    run = new_run(db_conn)
    if finish:
        billing.finish_run(db_conn, run["id"], now=NOW)
    with pytest.raises(LookupError):
        billing.reopen_run(db_conn, run["id"], now=NOW, trigger="retry")


def test_take_over_run_keeps_trigger_and_start(db_conn):
    run = billing.insert_run(db_conn, date(2026, 2, 1), now=NOW, trigger="catchUp")
    later = NOW + timedelta(hours=3)
    taken = billing.take_over_run(db_conn, run["id"], now=later)
    assert (taken["attempt"], taken["trigger"], taken["status"]) == (2, "catchUp", "running")
    assert (taken["started_at"], taken["heartbeat_at"]) == (NOW, later)


def test_take_over_run_only_takes_over_a_running_run(db_conn):
    run = new_run(db_conn)
    billing.finish_run(db_conn, run["id"], now=NOW)
    with pytest.raises(LookupError):
        billing.take_over_run(db_conn, run["id"], now=NOW)


def test_finish_run(db_conn):
    run = new_run(db_conn)
    done = billing.finish_run(db_conn, run["id"], now=NOW + timedelta(minutes=5))
    assert done["status"] == "completed"
    assert done["finished_at"] == NOW + timedelta(minutes=5)
    assert billing.get_run(db_conn, date(2026, 2, 1)) == done


def test_fail_run(db_conn):
    run = new_run(db_conn)
    failed = billing.fail_run(db_conn, run["id"], now=NOW, error="db down")
    assert (failed["status"], failed["error"]) == ("failed", "db down")
    assert failed["failure_kind"] == "run"  # the default: the worker must not move past it
    with pytest.raises(LookupError):
        billing.finish_run(db_conn, run["id"], now=NOW)  # only a running run can end


def test_fail_run_because_of_accounts(db_conn):
    run = new_run(db_conn)
    failed = billing.fail_run(
        db_conn, run["id"], now=NOW, error="1 of 3 accounts failed", failure_kind="accounts"
    )
    assert failed["failure_kind"] == "accounts"


def test_finish_run_has_no_failure_kind(db_conn):
    run = new_run(db_conn)
    assert billing.finish_run(db_conn, run["id"], now=NOW)["failure_kind"] is None


def test_get_run(db_conn):
    assert billing.get_run(db_conn, date(2026, 2, 1)) is None
    run = new_run(db_conn)
    assert billing.get_run(db_conn, date(2026, 2, 1)) == run


def test_insert_abr(db_conn):
    w = world(db_conn)
    abr = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)), amount="499.90")
    assert abr["charge_type"] == "recurring"
    assert abr["amount"] == Decimal("499.90")
    assert abr["period"] == days(date(2026, 1, 1), date(2026, 2, 1))
    assert abr["customer_bill_id"] is None


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
        currency="RUB",
        period=days(date(2026, 2, 15), date(2026, 3, 1)),
        billing_run_id=w.run["id"],
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


def test_insert_bill(db_conn):
    w = world(db_conn)
    bill = billing.insert_bill(
        db_conn,
        billing_account_id=w.account["id"],
        currency="RUB",
        bill_period=days(date(2026, 1, 1), date(2026, 2, 1)),
        bill_date=date(2026, 2, 1),
        amount_due=Decimal("499.90"),
        billing_run_id=w.run["id"],
    )
    assert (bill["state"], bill["amount_due"]) == ("new", Decimal("499.90"))
    # ABRs of the same account can then be attached to it
    abr = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)))
    db_conn.execute(
        "UPDATE applied_customer_billing_rate SET customer_bill_id = %s WHERE id = %s",
        (bill["id"], abr["id"]),
    )


def test_account_balance(db_conn):
    w = world(db_conn)
    second = product(db_conn, w.account["id"], w.offering["id"], date(2026, 1, 1))
    jan = charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)), amount="500.00")
    charge(db_conn, w, days(date(2026, 1, 1), date(2026, 2, 1)), "300.00", second["id"])
    billing.insert_abr(
        db_conn,
        product_id=w.product["id"],
        billing_account_id=w.account["id"],
        charge_type="refund",
        amount=Decimal("-161.29"),
        currency="RUB",
        period=days(date(2026, 1, 22), date(2026, 2, 1)),
        billing_run_id=w.run["id"],
        refunds_rate_id=jan["id"],
    )
    for amount in ("400.00", "250.50"):
        payments.insert_payment(
            db_conn,
            billing_account_id=w.account["id"],
            amount=Decimal(amount),
            currency="RUB",
            payment_method="bankTransfer",
            received_at=NOW,
        )
    # another account's money must not leak in
    other = account(db_conn, name="Petrov")
    payments.insert_payment(
        db_conn,
        billing_account_id=other["id"],
        amount=Decimal("999.00"),
        currency="RUB",
        payment_method="bankTransfer",
        received_at=NOW,
    )

    # payments 650.50 - (charges 800.00 + refund -161.29) = 11.79
    balance = billing.account_balance(db_conn, w.account["id"])
    assert isinstance(balance, Decimal)
    assert balance == Decimal("11.79")
    assert billing.account_balance(db_conn, other["id"]) == Decimal("999.00")


def test_account_balance_of_empty_account_is_zero(db_conn):
    acc = account(db_conn)
    assert billing.account_balance(db_conn, acc["id"]) == Decimal("0")


# --- payments ---


def test_insert_payment(db_conn):
    acc = account(db_conn)
    received = NOW.astimezone(MSK)
    pay = payments.insert_payment(
        db_conn,
        billing_account_id=acc["id"],
        amount=Decimal("1000.00"),
        currency="RUB",
        payment_method="bankTransfer",
        received_at=received,
    )
    assert (pay["status"], pay["amount"], pay["received_at"]) == (
        "done",
        Decimal("1000.00"),
        received,
    )
