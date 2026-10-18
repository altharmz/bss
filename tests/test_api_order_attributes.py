"""TMF622 slice 8a: every attribute of ProductOrder and ProductOrderItem, the derived
billing account, noChange items, nested items and draft orders
(docs/decisions/tmf-api.md, "Order attributes and drafts")."""

from datetime import timedelta

import pytest

from tests import apidata, timeline
from tests.apidata import ORDER
from tests.timeline import iso, z

LATER = timeline.START + timedelta(days=4, hours=12)

NOTE = {"@type": "Note", "text": "call before noon", "author": "Shop"}
CHANNEL = {
    "@type": "RelatedChannel",
    "role": "sales",
    "channel": {"@type": "ChannelRef", "id": "web", "name": "Web shop"},
}
QUOTE = {"@type": "QuoteRef", "id": "Q-7"}
MILESTONE = {"@type": "ProductOrderMilestone", "name": "Line ordered", "status": "Completed"}
APPOINTMENT = {"@type": "AppointmentRef", "id": "A-1"}


@pytest.fixture
def shop(api):
    offering, account = apidata.sellable_offering(api), apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))  # the prices start at START
    return offering, account


def _post(api, body):
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def _product_count(db_conn):
    return db_conn.execute("SELECT count(*) FROM product").fetchone()[0]


# --- documents ---


def test_order_and_item_documents_round_trip(api, shop):
    offering, acc = shop
    item = apidata.add_item(offering, note=[NOTE], appointment=APPOINTMENT)
    placed = apidata.order(
        api,
        acc,
        item,
        note=[NOTE],
        channel=[CHANNEL],
        quote=[QUOTE],
        productOrderMilestone=[MILESTONE],
        requestedCompletionDate=iso(LATER),
    )
    assert placed["state"] == "completed"
    assert placed["note"] == [NOTE] and placed["channel"] == [CHANNEL]
    assert placed["quote"] == [QUOTE] and placed["productOrderMilestone"] == [MILESTONE]
    assert placed["requestedCompletionDate"] == z(LATER)  # UTC, as all
    assert placed["requestedInitialState"] == "acknowledged"
    (stored,) = placed["productOrderItem"]
    assert stored["note"] == [NOTE] and stored["appointment"] == APPOINTMENT
    assert api.get(placed["href"]).json() == placed
    slim = api.get(f"{placed['href']}?fields=note,quote").json()
    assert slim["note"] == [NOTE] and "channel" not in slim

    patched = api.patch(placed["href"], {"note": None, "expectedCompletionDate": z(LATER)})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert "note" not in body and body["channel"] == [CHANNEL]
    assert body["expectedCompletionDate"] == z(LATER)


def test_expected_completion_date_is_patch_only(api, shop):
    """ProductOrder_FVO has no expectedCompletionDate, ProductOrder_MVO has."""
    offering, acc = shop
    body = apidata.order_body(acc, apidata.add_item(offering))
    response = api.post(ORDER, body | {"expectedCompletionDate": z(LATER)})
    assert response.status_code == 400
    assert "expectedCompletionDate: unknown attribute" in response.json()["message"]


def test_documents_are_checked_against_the_spec(api, shop, db_conn):
    offering, acc = shop
    for change, status in (
        ({"note": [{"text": "no type"}]}, 422),
        ({"channel": [CHANNEL | {"zzUnknown": 1}]}, 400),
        ({"productOrderMilestone": [MILESTONE | {"status": "done"}]}, 422),
        ({"requestedCompletionDate": "tomorrow"}, 422),
    ):
        body = apidata.order_body(acc, apidata.add_item(offering)) | change
        assert api.post(ORDER, body).status_code == status, change
    item = apidata.add_item(offering, appointment={"@type": "AppointmentRef"})  # no id
    response = api.post(ORDER, apidata.order_body(acc, item))
    assert response.status_code == 422
    assert "productOrderItem.0.appointment.id: Field required" in response.json()["message"]
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone()[0] == 0


def test_an_item_relationship_names_another_item_of_the_order(api, shop):
    offering, acc = shop
    relation = {"@type": "OrderItemRelationship", "relationshipType": "reliesOn"}
    first = apidata.add_item(offering, "1")
    second = apidata.add_item(offering, "2", productOrderItemRelationship=[relation | {"id": "1"}])
    placed = apidata.order(api, acc, first, second)
    assert placed["productOrderItem"][1]["productOrderItemRelationship"] == [relation | {"id": "1"}]
    for target in ("3", "2"):  # not in the order; the item itself
        bad = apidata.add_item(
            offering, "2", productOrderItemRelationship=[relation | {"id": target}]
        )
        response = api.post(ORDER, apidata.order_body(acc, first, bad))
        assert response.status_code == 422, target
        assert "not another item of this order" in response.json()["message"]


def test_an_item_may_repeat_what_processing_sets(api, shop):
    offering, acc = shop
    placed = apidata.order(api, acc, apidata.add_item(offering))
    (item,) = placed["productOrderItem"]
    again = apidata.add_item(
        offering, state="completed", itemPrice=item["itemPrice"], itemTotalPrice=item["itemPrice"]
    )
    assert apidata.order(api, acc, again)["productOrderItem"][0]["state"] == "completed"
    wrong = api.post(ORDER, apidata.order_body(acc, apidata.add_item(offering, state="rejected")))
    assert wrong.status_code == 422
    assert "productOrderItem 1: state is set by the server" in wrong.json()["message"]


# --- the billing account ---


def test_the_account_comes_from_an_item_or_a_product(api, shop):
    offering, acc = shop
    ref = {"id": acc["id"], "@type": "BillingAccountRef"}
    by_item = _post(
        api,
        {
            "@type": "ProductOrder",
            "productOrderItem": [apidata.add_item(offering, billingAccount=ref)],
        },
    )
    assert by_item["state"] == "completed"
    assert by_item["billingAccount"]["id"] == acc["id"]
    product = apidata.product_of(api, by_item)
    by_product = _post(
        api, {"@type": "ProductOrder", "productOrderItem": [apidata.delete_item(product["id"])]}
    )
    assert by_product["state"] == "completed"
    assert by_product["billingAccount"]["id"] == acc["id"]


def test_without_any_account_an_order_is_refused(api, shop, db_conn):
    offering, _ = shop
    response = api.post(
        ORDER, {"@type": "ProductOrder", "productOrderItem": [apidata.add_item(offering)]}
    )
    assert response.status_code == 422
    assert "an order needs a billingAccount" in response.json()["message"]
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone()[0] == 0


def test_items_of_two_accounts_are_rejected(api, shop):
    offering, acc = shop
    other = apidata.account(api, name="Other")
    items = [
        apidata.add_item(offering, "1", billingAccount={"id": acc["id"]}),
        apidata.add_item(offering, "2", billingAccount={"id": other["id"]}),
    ]
    rejected = _post(api, {"@type": "ProductOrder", "productOrderItem": items})
    assert rejected["state"] == "rejected"
    (message,) = rejected["productOrderErrorMessage"]
    assert message["code"] == "billingAccountMismatch"
    assert message["productOrderItem"][0]["productOrderItemId"] == "2"


# --- noChange ---


def _no_change(product_id, item_id="1"):
    return {**apidata.delete_item(product_id, item_id), "action": "noChange"}


def test_a_no_change_item_completes_and_changes_nothing(api, shop, db_conn):
    offering, acc = shop
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    placed = apidata.order(api, acc, _no_change(product["id"]), apidata.add_item(offering, "2"))
    assert placed["state"] == "completed"
    assert [i["state"] for i in placed["productOrderItem"]] == ["completed", "completed"]
    assert api.get(product["href"]).json() == product
    assert _product_count(db_conn) == 2
    # it is cancellable with its order: nothing of it to undo
    future = apidata.order(
        api,
        acc,
        _no_change(product["id"]),
        apidata.add_item(offering, "2"),
        requestedStartDate=apidata.iso(timeline.START + timedelta(days=3)),
    )
    cancel = api.post(
        f"{apidata.ORDER.rsplit('/', 1)[0]}/cancelProductOrder",
        {"@type": "CancelProductOrder", "productOrder": {"id": future["id"]}},
    )
    assert cancel.status_code == 201, cancel.text
    assert api.get(product["href"]).json() == product


def test_a_no_change_item_needs_a_live_product_of_the_account(api, shop):
    offering, acc = shop
    other = apidata.account(api, name="Other")
    product = apidata.product_of(api, apidata.order(api, acc, apidata.add_item(offering)))
    response = api.post(
        ORDER, apidata.order_body(acc, {**_no_change(product["id"])} | {"product": None})
    )
    assert response.status_code == 422
    foreign = apidata.order(api, other, _no_change(product["id"]))
    assert foreign["productOrderErrorMessage"][0]["code"] == "productOfOtherAccount"
    apidata.order(api, acc, apidata.delete_item(product["id"]))
    ended = apidata.order(api, acc, _no_change(product["id"]))
    assert ended["productOrderErrorMessage"][0]["code"] == "productAlreadyEnded"


# --- nested items ---


def test_nested_items_are_processed_and_kept_nested(api, shop, db_conn):
    offering, acc = shop
    child = apidata.add_item(offering, "1.1")
    parent = apidata.add_item(offering, "1", productOrderItem=[child])
    placed = apidata.order(api, acc, parent, apidata.add_item(offering, "2"))
    assert placed["state"] == "completed"
    (first, second) = placed["productOrderItem"]
    assert (first["id"], second["id"]) == ("1", "2")
    (nested,) = first["productOrderItem"]
    assert nested["id"] == "1.1" and nested["state"] == "completed"
    assert nested["product"]["id"] != first["product"]["id"]
    assert _product_count(db_conn) == 3
    assert api.get(placed["href"]).json() == placed


def test_a_nested_item_is_an_item_of_the_whole_order(api, shop, db_conn):
    offering, acc = shop
    duplicate = apidata.add_item(offering, "1", productOrderItem=[apidata.add_item(offering, "1")])
    assert api.post(ORDER, apidata.order_body(acc, duplicate)).status_code == 422
    bad_child = {**apidata.add_item(offering, "1.1"), "action": "modify"}
    rejected = apidata.order(
        api, acc, apidata.add_item(offering, "1", productOrderItem=[bad_child])
    )
    assert rejected["state"] == "rejected"
    (parent,) = rejected["productOrderItem"]
    assert parent["state"] == "cancelled"
    assert parent["productOrderItem"][0]["state"] == "rejected"
    assert _product_count(db_conn) == 0


# --- drafts ---


def _draft(api, *items, **over):
    return _post(
        api,
        {"@type": "ProductOrder", "requestedInitialState": "draft", "productOrderItem": list(items)}
        | over,
    )


def test_a_draft_is_stored_unprocessed(api, shop, db_conn):
    offering, acc = shop
    draft = _draft(api, apidata.add_item(offering))
    assert draft["state"] == "draft" and draft["requestedInitialState"] == "draft"
    assert "billingAccount" not in draft and "requestedStartDate" not in draft
    (item,) = draft["productOrderItem"]
    assert "state" not in item and "itemPrice" not in item and "product" not in item
    assert _product_count(db_conn) == 0
    assert api.get(draft["href"]).json() == draft


def test_a_draft_changes_and_is_submitted_by_patch(api, shop, db_conn):
    offering, acc = shop
    draft = _draft(api, apidata.add_item(offering), note=[NOTE])
    # nothing to process it with yet: an account is missing
    response = api.patch(draft["href"], {"state": "acknowledged"})
    assert response.status_code == 422
    assert api.get(draft["href"]).json() == draft
    changed = api.patch(
        draft["href"],
        {
            "billingAccount": {"id": acc["id"]},
            "productOrderItem": [apidata.add_item(offering, "a"), apidata.add_item(offering, "b")],
            "requestedStartDate": apidata.iso(timeline.START + timedelta(days=2)),
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["state"] == "draft" and body["billingAccount"]["id"] == acc["id"]
    assert [i["id"] for i in body["productOrderItem"]] == ["a", "b"]
    assert _product_count(db_conn) == 0

    api.clock.set(timeline.START + timedelta(days=1, hours=9))
    submitted = api.patch(draft["href"], {"state": "acknowledged"})
    assert submitted.status_code == 200, submitted.text
    order = submitted.json()
    assert order["state"] == "completed" and order["requestedInitialState"] == "draft"
    assert order["creationDate"] == draft["creationDate"]  # the draft's
    assert order["completionDate"] == z(timeline.START + timedelta(days=1, hours=9))
    assert order["note"] == [NOTE]
    assert [i["state"] for i in order["productOrderItem"]] == ["completed", "completed"]
    assert _product_count(db_conn) == 2
    # processed: what it was placed with is fixed now, and repeating it is fine
    assert api.patch(draft["href"], {"productOrderItem": []}).status_code == 422
    assert api.patch(draft["href"], {"billingAccount": {"id": acc["id"]}}).status_code == 200
    assert api.patch(draft["href"], {"requestedStartDate": None}).status_code == 409


def test_a_submitted_draft_can_be_rejected(api, shop, db_conn):
    offering, acc = shop
    draft = _draft(
        api,
        apidata.add_item(offering),
        billingAccount={"id": acc["id"]},
        requestedStartDate=apidata.iso(timeline.START + timedelta(days=1)),
    )
    api.clock.set(timeline.START + timedelta(days=3))  # its start date is in the past now
    order = api.patch(draft["href"], {"state": "acknowledged"}).json()
    assert order["state"] == "rejected"
    assert order["productOrderErrorMessage"][0]["code"] == "requestedStartDateInPast"
    assert _product_count(db_conn) == 0


def test_a_draft_item_of_another_account_is_refused(api, shop):
    offering, acc = shop
    other = apidata.account(api, name="Other")
    response = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "requestedInitialState": "draft",
            "billingAccount": {"id": acc["id"]},
            "productOrderItem": [apidata.add_item(offering, billingAccount={"id": other["id"]})],
        },
    )
    assert response.status_code == 422
    assert "differs from the order's" in response.json()["message"]


def test_a_draft_is_deleted_or_cancelled(api, shop):
    offering, acc = shop
    first = _draft(api, apidata.add_item(offering))
    assert api.delete(first["href"]).status_code == 204
    assert api.get(first["href"]).status_code == 404
    second = _draft(api, apidata.add_item(offering))
    cancel = api.post(
        f"{apidata.ORDER.rsplit('/', 1)[0]}/cancelProductOrder",
        {"@type": "CancelProductOrder", "productOrder": {"id": second["id"]}},
    )
    assert cancel.status_code == 201, cancel.text
    cancelled = api.get(second["href"]).json()
    assert cancelled["state"] == "cancelled"
    assert cancelled["productOrderItem"][0]["state"] == "cancelled"
    assert api.patch(second["href"], {"state": "acknowledged"}).status_code == 422


def test_a_draft_names_only_what_exists(api, shop):
    offering, acc = shop
    for over in (
        {"billingAccount": {"id": apidata.NIL}},
        {"productOrderItem": [apidata.add_item({"id": apidata.NIL})]},
        {"productOrderItem": [apidata.delete_item(apidata.NIL)]},
    ):
        body = {
            "@type": "ProductOrder",
            "requestedInitialState": "draft",
            "productOrderItem": [apidata.add_item(offering)],
        } | over
        assert api.post(ORDER, body).status_code == 422, over
