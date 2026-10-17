"""TMF622 productOrder: synchronous processing, rejections stored with reasons, and the
4xx cases that cannot be stored at all."""

from datetime import timedelta

import pytest

from app.repository import products
from tests import apidata, timeline
from tests.apidata import NIL, ORDER, PRODUCT
from tests.timeline import iso, midnight, z

# The catalog is created at timeline.NOW with prices from timeline.START (the next
# midnight); orders are placed a few hours after that, on TOMORROW.
OPEN = timeline.START + timedelta(hours=9)
TODAY = timeline.TOMORROW


@pytest.fixture
def shop(api):
    """An offering with a RUB monthly price and an Active RUB account; the clock is then
    moved to the day the price starts."""
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    api.clock.set(OPEN)
    return offering, acc


def product_count(db_conn) -> int:
    return db_conn.execute("SELECT count(*) FROM product").fetchone()[0]


def errors_of(order: dict) -> list[tuple]:
    return [
        (e["code"], [i["productOrderItemId"] for i in e.get("productOrderItem", [])])
        for e in order.get("productOrderErrorMessage", [])
    ]


# --- add ---


def test_add_creates_an_active_product(api, shop):
    offering, acc = shop
    created = api.post(ORDER, apidata.order_body(acc, apidata.add_item(offering)))
    assert created.status_code == 201
    order = created.json()
    assert created.headers["Location"] == order["href"] == f"{ORDER}/{order['id']}"
    assert order["state"] == "completed"
    assert order["creationDate"] == order["completionDate"] == z(OPEN)
    assert order["requestedStartDate"] == z(midnight(TODAY))  # defaulted to today
    assert "productOrderErrorMessage" not in order
    (item,) = order["productOrderItem"]
    assert (item["id"], item["action"], item["state"]) == ("1", "add", "completed")
    assert item["productOffering"]["id"] == offering["id"]

    product = apidata.product_of(api, order)
    assert product["status"] == "active"
    assert product["startDate"] == z(midnight(TODAY))
    assert product["billingAccount"]["id"] == acc["id"]
    (origin,) = product["productOrderItem"]
    assert (origin["orderId"], origin["orderItemId"]) == (order["id"], "1")
    assert api.get(order["href"]).json() == order


def test_requested_start_date_is_the_date_in_billing_tz(api):
    """2026-10-01T23:30:00Z is already 2026-10-02 in Moscow; the order and the product
    carry the normalized date."""
    offering = apidata.sellable_offering(api)  # price from START = midnight of TOMORROW
    acc = apidata.account(api)
    assert timeline.TOMORROW.isoformat() == "2026-10-02"
    order = apidata.order(
        api, acc, apidata.add_item(offering), requestedStartDate="2026-10-01T23:30:00Z"
    )
    assert order["state"] == "completed"
    assert order["requestedStartDate"] == "2026-10-01T21:00:00Z"  # 2026-10-02 00:00 MSK
    product = apidata.product_of(api, order)
    assert product["startDate"] == "2026-10-01T21:00:00Z"
    assert product["status"] == "created"  # starts tomorrow: activated by the stage 4 worker


# --- delete ---


def test_delete_terminates_a_started_product(api, shop, db_conn):
    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    api.clock.set(OPEN + timedelta(days=10))
    order = apidata.order(api, acc, apidata.delete_item(product["id"]))
    assert order["state"] == "completed"
    assert order["productOrderItem"][0]["product"]["id"] == product["id"]
    ended = api.get(product["href"]).json()
    assert ended["status"] == "terminated"
    assert ended["terminationDate"] == z(midnight(TODAY + timedelta(days=10)))
    roles = [(i["orderId"], i["role"]) for i in ended["productOrderItem"]]
    assert roles[1] == (order["id"], "termination")


def test_delete_with_a_future_date_is_pending_until_then(api, shop, db_conn):
    """Mirrors `created` for a future start: the product keeps being billed until the
    termination date, and the stage 4 worker flips the status on that date."""
    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    end = TODAY + timedelta(days=20)
    order = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(end))
    )
    assert order["state"] == "completed"
    pending = api.get(product["href"]).json()
    assert pending["status"] == "pendingTerminate"
    assert pending["terminationDate"] == z(midnight(end))
    account_id = _uuid(acc["id"])
    assert len(products.list_active_products(db_conn, account_id, end - timedelta(days=1))) == 1
    assert products.list_active_products(db_conn, account_id, end) == []
    # it already has its delete: a second one is rejected
    again = apidata.order(api, acc, apidata.delete_item(product["id"]))
    assert errors_of(again) == [("productAlreadyEnded", ["1"])]


def test_delete_of_a_future_product_cancels_it_with_zero_billable_days(api, shop, db_conn):
    offering, acc = shop
    start = TODAY + timedelta(days=5)
    placed = apidata.order(
        api, acc, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    product = apidata.product_of(api, placed)
    assert product["status"] == "created"

    later = apidata.delete_item(product["id"])
    rejected = apidata.order(
        api, acc, later, requestedStartDate=iso(midnight(start + timedelta(days=1)))
    )
    assert rejected["state"] == "rejected"
    assert errors_of(rejected) == [("productNotStarted", ["1"])]

    order = apidata.order(api, acc, apidata.delete_item(product["id"]))  # today
    assert order["state"] == "completed"
    cancelled = api.get(product["href"]).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["terminationDate"] == cancelled["startDate"] == z(midnight(start))
    for day in (TODAY, start, start + timedelta(days=30)):
        assert products.list_active_products(db_conn, _uuid(acc["id"]), day) == []


def _uuid(value: str):
    from uuid import UUID

    return UUID(value)


# --- rejections: stored, 201 ---


def test_modify_and_no_change_are_stored_as_rejected(api, shop, db_conn):
    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    for action in ("modify", "noChange"):
        item = {
            "@type": "ProductOrderItem",
            "id": "1",
            "action": action,
            "product": {"id": product["id"], "@type": "ProductRef"},
        }
        response = api.post(ORDER, apidata.order_body(acc, item))
        assert response.status_code == 201
        order = response.json()
        assert order["state"] == "rejected"
        assert order["productOrderItem"][0]["state"] == "rejected"
        (message,) = order["productOrderErrorMessage"]
        assert message["code"] == "actionNotSupported"
        assert message["reason"] == f"action {action} is not supported"
        assert message["timestamp"] == z(OPEN)
        assert api.get(order["href"]).json() == order
    assert product_count(db_conn) == 1


def test_one_bad_item_rejects_the_whole_order(api, shop, db_conn):
    offering, acc = shop
    good = apidata.add_item(offering, "1")
    bad = {**apidata.add_item(offering, "2"), "action": "modify"}
    order = apidata.order(api, acc, good, bad)
    assert order["state"] == "rejected"
    states = {i["id"]: i["state"] for i in order["productOrderItem"]}
    assert states == {"1": "cancelled", "2": "rejected"}
    assert errors_of(order) == [("actionNotSupported", ["2"])]
    assert product_count(db_conn) == 0


def test_account_must_be_active_and_start_not_in_the_past(api, shop, db_conn):
    offering, acc = shop
    past = apidata.order(
        api,
        acc,
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(TODAY - timedelta(days=1))),
    )
    # yesterday the price was not valid yet either; the order-level reason comes first
    assert errors_of(past)[0] == ("requestedStartDateInPast", [])
    assert api.patch(acc["href"], {"state": "Suspended"}).status_code == 200
    suspended = apidata.order(api, acc, apidata.add_item(offering))
    assert suspended["state"] == "rejected"
    assert errors_of(suspended) == [("accountNotActive", [])]
    assert product_count(db_conn) == 0


def test_offering_must_be_sellable_and_priced(api, shop, db_conn):
    offering, acc = shop
    eur = apidata.account(api, currency="EUR")
    assert errors_of(apidata.order(api, eur, apidata.add_item(offering))) == [
        ("noRecurringPrice", ["1"])
    ]
    assert api.patch(offering["href"], {"isSellable": False}).status_code == 200
    assert errors_of(apidata.order(api, acc, apidata.add_item(offering))) == [
        ("offeringNotSellable", ["1"])
    ]
    assert product_count(db_conn) == 0


def test_offering_and_price_must_be_valid_at_the_start_date(api, db_conn):
    offering = apidata.sellable_offering(api)  # valid and priced from START
    acc = apidata.account(api)
    today = apidata.order(api, acc, apidata.add_item(offering))  # NOW is before START
    assert errors_of(today) == [("noRecurringPrice", ["1"]), ("offeringNotValid", ["1"])]
    assert product_count(db_conn) == 0


def test_delete_rules(api, shop, db_conn):
    offering, acc = shop
    other = apidata.account(api, name="Other")
    mine = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    theirs = apidata.product_of(api, apidata.order(api, other, apidata.add_item(offering)))

    wrong_account = apidata.order(api, acc, apidata.delete_item(theirs["id"]))
    assert errors_of(wrong_account) == [("productOfOtherAccount", ["1"])]
    assert apidata.order(api, acc, apidata.delete_item(mine["id"]))["state"] == "completed"
    again = apidata.order(api, acc, apidata.delete_item(mine["id"]))
    assert errors_of(again) == [("productAlreadyEnded", ["1"])]
    mismatch = apidata.delete_item(
        theirs["id"], billingAccount={"id": acc["id"], "@type": "BillingAccountRef"}
    )
    assert errors_of(apidata.order(api, other, mismatch)) == [("billingAccountMismatch", ["1"])]
    assert api.get(theirs["href"]).json()["status"] == "active"


# --- 4xx: nothing stored ---


def _orders(db_conn) -> int:
    return db_conn.execute("SELECT count(*) FROM product_order").fetchone()[0]


@pytest.mark.parametrize(
    ("mutate", "status"),
    [
        (lambda b: b | {"productOrderItem": []}, 400),
        (lambda b: {k: v for k, v in b.items() if k != "productOrderItem"}, 422),
        (lambda b: {k: v for k, v in b.items() if k != "billingAccount"}, 422),
        (lambda b: b | {"billingAccount": {"id": NIL}}, 422),
        (lambda b: b | {"note": [{"text": "please"}]}, 400),  # not stored: unknown
        (lambda b: b | {"state": "acknowledged"}, 422),  # the server's: never set
        (lambda b: b | {"requestedStartDate": "2026-10-02T00:00:00"}, 422),
        (lambda b: _items(b, lambda i: i | {"productOffering": {"id": NIL}}), 422),
        (
            lambda b: _items(b, lambda i: {k: v for k, v in i.items() if k != "productOffering"}),
            422,
        ),
        (lambda b: _items(b, lambda i: {k: v for k, v in i.items() if k != "id"}), 422),
        (lambda b: _items(b, lambda i: i | {"quantity": 2}), 422),
        (lambda b: _items(b, lambda i: i | {"action": "suspend"}), 422),
        (lambda b: _items(b, lambda i: i | {"product": {"id": NIL}}), 422),
        (lambda b: _items(b, lambda i: i | {"itemPrice": []}), 400),
        (lambda b: b | {"productOrderItem": b["productOrderItem"] * 2}, 422),
    ],
    ids=[
        "no items",
        "items missing",
        "account missing",
        "unknown account",
        "unknown attribute",
        "state is the server's",
        "date without offset",
        "unknown offering",
        "add without offering",
        "item without id",
        "quantity 2",
        "unknown action",
        "add with a product",
        "unknown item attribute",
        "duplicate item ids",
    ],
)
def test_malformed_orders_are_4xx_and_not_stored(api, shop, db_conn, mutate, status):
    offering, acc = shop
    response = api.post(ORDER, mutate(apidata.order_body(acc, apidata.add_item(offering))))
    assert response.status_code == status, response.text
    assert _orders(db_conn) == 0


def _items(body, change):
    return body | {"productOrderItem": [change(i) for i in body["productOrderItem"]]}


def test_malformed_delete_items(api, shop, db_conn):
    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    before = _orders(db_conn)
    no_product = {"@type": "ProductOrderItem", "id": "1", "action": "delete"}
    for items in (
        [no_product],
        [apidata.delete_item(NIL)],
        [apidata.delete_item(product["id"], "1"), apidata.delete_item(product["id"], "2")],
        [apidata.delete_item(product["id"]) | {"productOffering": {"id": offering["id"]}}],
    ):
        response = api.post(ORDER, apidata.order_body(acc, *items))
        assert response.status_code == 422, items
    assert _orders(db_conn) == before
    assert api.get(product["href"]).json()["status"] == "active"


# --- reading ---


def test_order_list_and_filters(api, shop):
    offering, acc = shop
    other = apidata.account(api, name="Other")
    apidata.order(api, acc, apidata.add_item(offering))
    apidata.order(api, acc, {**apidata.add_item(offering), "action": "modify"})
    apidata.order(api, other, apidata.add_item(offering))
    listed = api.get(f"{ORDER}?billingAccount.id={acc['id']}")
    assert listed.headers["X-Total-Count"] == "2"
    assert [o["state"] for o in api.get(f"{ORDER}?state=rejected").json()] == ["rejected"]
    slim = api.get(f"{ORDER}?fields=state").json()
    assert all(set(o) == {"id", "href", "@type", "state"} for o in slim)
    assert len(api.get(PRODUCT).json()) == 2


@pytest.mark.parametrize("method", ["PUT"])
def test_orders_cannot_be_replaced(api, shop, method):
    """PATCH (descriptive attributes): tests/test_api_order_patch.py; DELETE (rejected
    orders only) and cancelProductOrder: tests/test_api_order_cancel.py."""
    offering, acc = shop
    href = apidata.order(api, acc, apidata.add_item(offering))["href"]
    assert api.request(method, href, json={}).status_code == 405


def test_a_product_with_an_open_delete_elsewhere_is_rejected(api, shop, db_conn):
    """Orders are processed synchronously, so an open delete item only exists if some
    other writer (e.g. a future asynchronous flow) left one. Planted here directly."""
    from app.repository import orders

    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    orders.create_order(
        db_conn,
        billing_account_id=_uuid(acc["id"]),
        order_date=OPEN,
        items=[orders.NewOrderItem(item_id="9", action="delete", product_id=_uuid(product["id"]))],
    )
    db_conn.commit()
    order = apidata.order(api, acc, apidata.delete_item(product["id"]))
    assert errors_of(order) == [("openDeleteItem", ["1"])]
    assert api.get(product["href"]).json()["status"] == "active"
