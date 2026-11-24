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

from app.ordering.process import ItemRequest, OrderRequest, place_order
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
