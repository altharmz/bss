"""Status flips at the start of a billing run (docs/archive/stage4-design.md section 5; Q8).

created / pendingActive -> active once the start date is reached, pendingTerminate ->
terminated once the termination date is, only for what the run sees (the order completed
before 00:00 of the run date) and whatever the account's billing state. The visibility
boundary cannot decide anything for data made through the API (a product starting by D
whose order completed on D starts on D and is active at once; a termination due by D was
ordered earlier), so it is tested here with orders completed exactly around midnight.
"""

import threading
import time
from datetime import date, datetime
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import visible_before
from app.repository import orders, products
from app.worker import db as worker_db
from app.worker.process import bill_date
from tests import apidata, timeline
from tests.apiclient import decimals
from tests.builders.worker.worker_process import DEC1, DEC2, DEC3, NOV20, add, at, scenario
from tests.factories import MSK, NOW, account, add_order, offering
from tests.timeline import TZ, iso, midnight

D = date(2026, 2, 1)
BEFORE = visible_before(D, MSK)  # 2026-02-01 00:00 MSK
LAST_SECOND = datetime(2026, 1, 31, 23, 59, 59, tzinfo=MSK)
JAN20 = datetime(2026, 1, 20, 10, 0, tzinfo=MSK)


def ordered(conn, acc, off, *, start: date, status: str = "created", completed=JAN20):
    """A product created by an add order that completed at `completed`."""
    order = add_order(conn, acc["id"], off["id"])
    orders.set_order_state(conn, order["id"], "completed", completion_date=completed)
    return products.create_product(
        conn,
        billing_account_id=acc["id"],
        product_offering_id=off["id"],
        created_by_item_id=order["items"][0]["id"],
        start_date=start,
        status=status,
    )


def pending_termination(conn, acc, product, *, effective: date, completed=JAN20):
    """`product` (active) is pendingTerminate as of `effective`, by a delete order that
    completed at `completed`."""
    order = orders.create_order(
        conn,
        billing_account_id=acc["id"],
        order_date=NOW,
        items=[orders.NewOrderItem(item_id="1", action="delete", product_id=product["id"])],
    )
    orders.set_order_state(conn, order["id"], "completed", completion_date=completed)
    return products.terminate_product(
        conn,
        product["id"],
        termination_date=effective,
        terminated_by_item_id=order["items"][0]["id"],
        status="pendingTerminate",
    )


def status(conn, product) -> str:
    return products.get_product(conn, product["id"])["status"]


@pytest.fixture
def owned(db_conn):
    """An offering and an account in the test database."""
    return offering(db_conn), account(db_conn)


# --- by dates, for what the run sees ---


@pytest.mark.capability("worker.statusFlips")
def test_flips_follow_the_dates(db_conn, owned):
    off, acc = owned
    before = ordered(db_conn, acc, off, start=date(2026, 1, 31))
    on = ordered(db_conn, acc, off, start=D)
    after = ordered(db_conn, acc, off, start=date(2026, 2, 2))
    ends_on = pending_termination(
        db_conn,
        acc,
        ordered(db_conn, acc, off, start=date(2026, 1, 1), status="active"),
        effective=D,
    )
    ends_later = pending_termination(
        db_conn,
        acc,
        ordered(db_conn, acc, off, start=date(2026, 1, 1), status="active"),
        effective=date(2026, 2, 2),
    )
    counts = products.flip_statuses(db_conn, D, visible_before=BEFORE)
    assert counts == {"active": 2, "terminated": 1}
    assert [status(db_conn, p) for p in (before, on, after)] == ["active", "active", "created"]
    assert [status(db_conn, p) for p in (ends_on, ends_later)] == ["terminated", "pendingTerminate"]
    # idempotent: the same run again flips nothing
    assert products.flip_statuses(db_conn, D, visible_before=BEFORE) == {}


@pytest.mark.capability("worker.statusFlips")
def test_only_orders_completed_before_midnight_count(db_conn, owned):
    off, acc = owned
    seen = ordered(db_conn, acc, off, start=D, completed=LAST_SECOND)
    unseen = ordered(db_conn, acc, off, start=D, completed=BEFORE)  # 00:00:00 - too late
    ending_seen = pending_termination(
        db_conn,
        acc,
        ordered(db_conn, acc, off, start=date(2026, 1, 1), status="active"),
        effective=D,
        completed=LAST_SECOND,
    )
    ending_unseen = pending_termination(
        db_conn,
        acc,
        ordered(db_conn, acc, off, start=date(2026, 1, 1), status="active"),
        effective=D,
        completed=BEFORE,
    )
    products.flip_statuses(db_conn, D, visible_before=BEFORE)
    assert (status(db_conn, seen), status(db_conn, unseen)) == ("active", "created")
    assert (status(db_conn, ending_seen), status(db_conn, ending_unseen)) == (
        "terminated",
        "pendingTerminate",
    )


@pytest.mark.capability("worker.statusFlips")
@pytest.mark.parametrize("ended", ["cancelled", "aborted", "suspended", "active", "terminated"])
def test_other_statuses_are_never_touched(db_conn, owned, ended):
    off, acc = owned
    product = ordered(db_conn, acc, off, start=date(2026, 1, 10), status=ended)
    assert products.flip_statuses(db_conn, D, visible_before=BEFORE) == {}
    assert status(db_conn, product) == ended


# --- through the worker, read back through TMF637 ---


@pytest.mark.capability("worker.statusFlips")
def test_the_runs_flip_what_the_dates_say(api, migrated_db_url):
    api.clock.set(timeline.NOW)
    off = apidata.sellable_offering(api, value=300)
    api.clock.set(at(NOV20, 9))
    acc = apidata.account(api)
    later = add(api, acc, off, DEC3, on=at(NOV20))  # created until Dec 3
    first = add(api, acc, off, DEC1, on=at(NOV20))  # created until Dec 1
    assert (later["status"], first["status"]) == ("created", "created")
    worker = worker_db.connect(migrated_db_url)

    def product_status(product) -> str:
        return decimals(api.get(product["href"]))["status"]

    try:
        api.clock.set(at(DEC1, 0, 30))
        bill_date(worker, DEC1, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (product_status(first), product_status(later)) == ("active", "created")
        api.clock.set(at(DEC1))  # first ends as of Dec 3, ordered on Dec 1
        placed = apidata.order(
            api, acc, apidata.delete_item(first["id"]), requestedStartDate=iso(midnight(DEC3))
        )
        assert placed["state"] == "completed"
        assert product_status(first) == "pendingTerminate"
        api.clock.set(at(DEC2, 0, 30))
        bill_date(worker, DEC2, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (product_status(first), product_status(later)) == ("pendingTerminate", "created")
        api.clock.set(at(DEC3, 0, 30))
        bill_date(worker, DEC3, clock=api.clock, tz=TZ, trigger="scheduled")
        assert (product_status(first), product_status(later)) == ("terminated", "active")
    finally:
        worker.close()


@pytest.mark.capability("worker.statusFlips")
def test_a_blocked_accounts_products_are_flipped_too(api, migrated_db_url):
    """Statuses follow dates, not billing (D-W5): x fails on Dec 1 and is blocked after,
    yet its product turns active on Dec 1 and terminated on Dec 3."""
    worker = worker_db.connect(migrated_db_url)
    try:
        s = scenario(api, worker, broken=True)
        x = s["ids"]["x"]
        statuses = worker.execute(
            "SELECT status::text FROM product WHERE billing_account_id = %s", (x,)
        ).fetchall()
        assert statuses == [("terminated",)]
        blocked = worker.execute(
            "SELECT count(*) FROM billing_run_account WHERE billing_account_id = %s"
            " AND status = 'blocked'",
            (x,),
        ).fetchone()[0]
        assert blocked == 2  # Dec 2 and Dec 3
    finally:
        worker.close()


# --- Q8: the flips and an order lock products in the same (id) order ---


@pytest.mark.capability("worker.statusFlips")
def test_flips_and_a_delete_order_never_deadlock(db_conn, owned, migrated_db_url):
    """An order deleting two products locks them in id order (products.lock_products),
    before it validates - even pendingTerminate ones it then rejects. The flips lock theirs
    in id order too, so the two only ever wait for each other: the order holds the first,
    the flips wait for it without holding the second, the order takes the second."""
    off, acc = owned
    pair = sorted(
        (
            pending_termination(
                db_conn,
                acc,
                ordered(db_conn, acc, off, start=date(2026, 1, 1), status="active"),
                effective=D,
            )
            for _ in range(2)
        ),
        key=lambda p: p["id"],
    )
    first_id, second_id = (UUID(str(p["id"])) for p in pair)
    db_conn.commit()

    order_tx = psycopg.connect(migrated_db_url)
    worker = worker_db.connect(migrated_db_url)
    outcome: dict = {}

    def flips():
        try:
            with worker.transaction():
                outcome["counts"] = products.flip_statuses(worker, D, visible_before=BEFORE)
        except Exception as error:  # reported by the assertions below
            outcome["error"] = error

    try:
        order_tx.execute("SET statement_timeout = '10s'")
        products.lock_products(order_tx, [first_id])  # the order holds the first product
        thread = threading.Thread(target=flips)
        thread.start()
        waiting = "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s"
        with psycopg.connect(migrated_db_url, autocommit=True) as admin:
            for _ in range(100):  # until the flips are blocked on a lock
                row = admin.execute(waiting, (worker.info.backend_pid,)).fetchone()
                if row and row[0] == "Lock":
                    break
                time.sleep(0.05)
            else:
                pytest.fail("the flips never waited for the order's lock")
        products.lock_products(order_tx, [second_id])  # the order takes the second
        order_tx.commit()  # the order is done; the flips go on
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        order_tx.close()
        worker.close()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["counts"] == {"terminated": 2}
