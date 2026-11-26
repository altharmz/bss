"""Two orders deleting the same product at the same time, on two connections.

The first order holds the product row lock (SELECT ... FOR UPDATE) until it commits. The
second waits for it, then sees the product already terminated and is stored as rejected
with a reason - instead of terminating it a second time, or colliding with the first on
the one-open-delete unique index (which would abort its transaction, and a rejected
order that cannot be stored is lost).
"""

import threading
from datetime import timedelta
from uuid import UUID

import psycopg
import pytest

from app.ordering import process
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import parties
from tests import apidata, timeline
from tests.apidata import ORDER


def test_a_concurrent_second_delete_waits_and_is_rejected(api, migrated_db_url):
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    now = timeline.START + timedelta(hours=9)
    api.clock.set(now)
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))

    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            place_order(
                first,
                OrderRequest(
                    billing_account_id=UUID(acc["id"]),
                    items=(ItemRequest("1", "delete", product_id=UUID(product["id"])),),
                ),
                now=now,
                today=timeline.TOMORROW,
            )
            # the first order is processed but not committed; it holds the row lock
            result = {}
            second = threading.Thread(
                target=lambda: result.update(
                    response=api.post(
                        ORDER, apidata.order_body(acc, apidata.delete_item(product["id"]))
                    )
                )
            )
            second.start()
            second.join(timeout=2)
            assert second.is_alive(), "the second order did not wait for the first"
        # leaving the block commits the first order and releases the lock
        second.join(timeout=10)
        assert not second.is_alive()
    finally:
        first.close()

    response = result["response"]
    assert response.status_code == 201
    order = response.json()
    assert order["state"] == "rejected"
    (message,) = order["productOrderErrorMessage"]
    assert message["code"] == "productAlreadyEnded"
    assert message["reason"] == "the product is already terminated"

    ended = api.get(product["href"]).json()
    assert ended["status"] == "terminated"
    creation, termination = ended["productOrderItem"]
    assert termination["orderId"] != order["id"]  # terminated by the first order only


# --- the account changes between the unlocked look and the lock ---


@pytest.fixture
def rival(db_conn):
    """Another session; it fails rather than waits on a lock the request holds."""
    db_conn.execute("SET lock_timeout = '5s'")
    db_conn.commit()
    return db_conn


def race_on_account_lock(monkeypatch, competing):
    """Run `competing` (committed in another session) right after the order's first,
    unlocked read of its account and before it locks the account."""
    real = parties.get_billing_account

    def raced(conn, uid, lock=None):
        if lock is not None:
            competing()
        return real(conn, uid, lock)

    monkeypatch.setattr(process.parties, "get_billing_account", raced)


@pytest.mark.parametrize(
    "sql,message",
    [
        (
            "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s",
            "billing account mode changed; retry the order",
        ),
        ("DELETE FROM billing_account WHERE id = %s", "does not exist"),
    ],
)
def test_an_account_that_changes_before_its_lock_refuses_the_order(
    api, rival, monkeypatch, sql, message
):
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))

    def competing():
        rival.execute(sql, (acc["id"],))
        rival.commit()

    race_on_account_lock(monkeypatch, competing)
    response = api.post(ORDER, apidata.order_body(acc, apidata.add_item(offering)))
    assert response.status_code == 422, response.text
    assert message in response.json()["message"]
    assert rival.execute("SELECT count(*) FROM product_order").fetchone()[0] == 0
    assert rival.execute("SELECT count(*) FROM product").fetchone()[0] == 0
