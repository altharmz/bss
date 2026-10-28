"""Number claims are order-owned, interval checked and atomically reversible."""

import threading
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from app.config import settings
from app.ordering.process import ItemRequest, OrderRequest, place_order
from tests import apidata, timeline
from tests.apidata import ORDER, PRODUCT
from tests.test_api_order_cancel import CANCEL
from tests.test_api_resource_inventory import number
from tests.test_events import stored, subscribe


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api)
    spec = offering["productSpecification"]
    response = api.patch(
        spec["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    return offering, account


def refs(uid):
    return [{"id": uid, "@type": "ResourceRef", "@referredType": "LogicalResource"}]


@pytest.mark.parametrize(
    "source_requires,target_requires", [(False, True), (True, False), (False, False), (True, True)]
)
def test_offering_change_preserves_number_requirement(api, source_requires, target_requires):
    source = apidata.sellable_offering(api, name="Source")
    target = apidata.sellable_offering(api, name="Target")
    for offering, required in [(source, source_requires), (target, target_requires)]:
        if required:
            response = api.patch(
                offering["productSpecification"]["href"],
                {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
            )
            assert response.status_code == 200, response.text
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    uid = number(api)["id"] if source_requires else None
    purchase = add(api, (source, account), uid)
    product = apidata.product_of(api, purchase)
    order = apidata.order(
        api,
        account,
        {
            "id": "1",
            "action": "modify",
            "product": {"id": product["id"], "@type": "ProductRef"},
            "productOffering": {"id": target["id"], "@type": "ProductOfferingRef"},
        },
    )
    if source_requires != target_requires:
        assert order["state"] == "rejected"
        assert [error["code"] for error in order["productOrderErrorMessage"]] == [
            "numberRequirementDiffers"
        ]
        assert api.get(product["href"]).json()["productOffering"]["id"] == source["id"]
    else:
        assert order["state"] == "completed"
        assert api.get(product["href"]).json()["productOffering"]["id"] == target["id"]
    if uid:
        assert api.get(product["href"]).json()["realizingResource"][0]["id"] == uid


def add(api, shop, uid=None, day=None):
    offering, account = shop
    item = apidata.add_item(offering)
    if uid is not None:
        item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(uid)}
    body = apidata.order_body(account, item)
    if day is not None:
        body["requestedStartDate"] = timeline.iso(timeline.midnight(day))
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def cancel(api, order):
    return api.post(
        CANCEL,
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": order["id"], "@type": "ProductOrderRef"},
        },
    )


def modify(api, account, product, uid, day):
    body = apidata.order_body(
        account,
        {
            "id": "1",
            "action": "modify",
            "product": {
                "id": product["id"],
                "@type": "ProductRefOrValue",
                "realizingResource": refs(uid),
            },
        },
    )
    body["requestedStartDate"] = timeline.iso(timeline.midnight(day))
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def test_assignment_lookup_release_and_requirement_guard(api, shop):
    n = number(api)
    order = add(api, shop, n["id"])
    assert order["state"] == "completed"
    product = apidata.product_of(api, order)
    assert product["realizingResource"][0]["id"] == n["id"]
    assert api.get(n["href"]).json()["usageState"] == "busy"
    result = api.get(f"{PRODUCT}?realizingResource.id={n['id']}")
    assert [p["id"] for p in result.json()] == [product["id"]]
    assert (
        api.patch(
            shop[0]["productSpecification"]["href"], {"resourceSpecification": []}
        ).status_code
        == 409
    )
    assert api.patch(product["href"], {"realizingResource": []}).status_code == 409
    ended = apidata.order(api, shop[1], apidata.delete_item(product["id"]))
    assert ended["state"] == "completed"
    assert api.get(n["href"]).json()["allocationStatus"] == "available"
    assert api.get(f"{PRODUCT}?realizingResource.id={n['id']}").json() == []
    assert add(api, shop, n["id"])["state"] == "completed"


@pytest.mark.parametrize("case", ["missing", "unknown", "locked", "busy", "future"])
def test_unavailable_numbers_reject_without_products(api, shop, db_conn, case):
    n = number(api)
    uid = n["id"]
    if case == "missing":
        uid = None
    elif case == "unknown":
        uid = str(uuid4())
    elif case == "locked":
        assert api.patch(n["href"], {"administrativeState": "locked"}).status_code == 200
    elif case in ("busy", "future"):
        day = timeline.TOMORROW + timedelta(days=10) if case == "future" else None
        assert add(api, shop, uid, day)["state"] == "completed"
    count = db_conn.execute("SELECT count(*) FROM product").fetchone()[0]
    assert add(api, shop, uid)["state"] == "rejected"
    assert db_conn.execute("SELECT count(*) FROM product").fetchone()[0] == count


def test_future_add_cancel_retains_history(api, shop, db_conn):
    n = number(api)
    order = add(api, shop, n["id"], timeline.TOMORROW + timedelta(days=3))
    assert api.get(n["href"]).json()["allocationStatus"] == "reserved"
    assert cancel(api, order).status_code == 201
    assert api.get(n["href"]).json()["allocationStatus"] == "available"
    assert db_conn.execute("SELECT cancelled_at IS NOT NULL FROM number_assignment").fetchone()[0]
    assert api.delete(n["href"]).status_code == 409


def test_number_change_and_cancellation_conflict(api, shop):
    old, new = number(api), number(api, "12025550124")
    product = apidata.product_of(api, add(api, shop, old["id"]))
    day = timeline.TOMORROW + timedelta(days=3)
    change = modify(api, shop[1], product, new["id"], day)
    assert change["state"] == "completed"
    assert api.get(product["href"]).json()["realizingResource"][0]["id"] == old["id"]
    assert cancel(api, change).status_code == 201
    change = modify(api, shop[1], product, new["id"], day)
    reused = add(api, shop, old["id"], day)
    assert reused["state"] == "completed"
    assert cancel(api, change).status_code == 409
    api.clock.set(timeline.midnight(day))
    assert api.get(product["href"]).json()["realizingResource"][0]["id"] == new["id"]


@pytest.mark.parametrize("days", [0, 3])
def test_quarantine_starts_at_effective_release(api, shop, monkeypatch, days):
    monkeypatch.setattr(settings, "msisdn_quarantine_days", days)
    n = number(api)
    product = apidata.product_of(api, add(api, shop, n["id"]))
    day = timeline.TOMORROW + timedelta(days=2)
    order = apidata.order(
        api,
        shop[1],
        apidata.delete_item(product["id"]),
        requestedStartDate=timeline.iso(timeline.midnight(day)),
    )
    assert order["state"] == "completed"
    assert api.get(n["href"]).json()["usageState"] == "busy"
    if days:
        assert add(api, shop, n["id"], day)["state"] == "rejected"
    assert add(api, shop, n["id"], day + timedelta(days=days))["state"] == "completed"


def test_draft_claims_at_submission(api, shop):
    n = number(api)
    body = apidata.order_body(
        shop[1],
        {
            **apidata.add_item(shop[0]),
            "product": {"@type": "ProductRefOrValue", "realizingResource": refs(n["id"])},
        },
    )
    body["requestedInitialState"] = "draft"
    draft = api.post(ORDER, body)
    assert draft.status_code == 201, draft.text
    assert api.get(n["href"]).json()["allocationStatus"] == "available"
    submitted = api.patch(draft.json()["href"], {"state": "acknowledged"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "completed"


def test_unknown_nested_resource_attribute_is_refused(api, shop):
    item = apidata.add_item(shop[0])
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": [{**refs(str(uuid4()))[0], "unknown": True}],
    }
    assert api.post(ORDER, apidata.order_body(shop[1], item)).status_code == 400


def test_concurrent_number_claim_waits_and_rejects(api, shop, migrated_db_url):
    n = number(api)
    result = {}
    with psycopg.connect(migrated_db_url) as first:
        with first.transaction():
            place_order(
                first,
                OrderRequest(
                    UUID(shop[1]["id"]),
                    (
                        ItemRequest(
                            "1",
                            "add",
                            product_offering_id=UUID(shop[0]["id"]),
                            details={"product": {"realizingResource": refs(n["id"])}},
                        ),
                    ),
                ),
                now=api.clock.now(),
                today=api.clock.today(),
            )
            second = threading.Thread(target=lambda: result.update(order=add(api, shop, n["id"])))
            second.start()
            second.join(timeout=1)
            assert second.is_alive(), "second claim must wait for the number lock"
        second.join(timeout=10)
        assert not second.is_alive()
    assert result["order"]["state"] == "rejected"


def test_future_release_cancel_conflict_is_atomic(api, shop):
    n = number(api)
    product = apidata.product_of(api, add(api, shop, n["id"]))
    day = timeline.TOMORROW + timedelta(days=3)
    release = apidata.order(
        api,
        shop[1],
        apidata.delete_item(product["id"]),
        requestedStartDate=timeline.iso(timeline.midnight(day)),
    )
    assert add(api, shop, n["id"], day)["state"] == "completed"
    assert cancel(api, release).status_code == 409
    assert api.get(release["href"]).json()["state"] == "completed"
    assert "terminationDate" in api.get(product["href"]).json()


def test_setting_increase_and_lock_preserve_accepted_future_claim(api, shop, monkeypatch):
    n = number(api)
    product = apidata.product_of(api, add(api, shop, n["id"]))
    day = timeline.TOMORROW + timedelta(days=3)
    apidata.order(
        api,
        shop[1],
        apidata.delete_item(product["id"]),
        requestedStartDate=timeline.iso(timeline.midnight(day)),
    )
    accepted = add(api, shop, n["id"], day)
    monkeypatch.setattr(settings, "msisdn_quarantine_days", 10)
    assert api.patch(n["href"], {"administrativeState": "locked"}).status_code == 200
    api.clock.set(timeline.midnight(day))
    assert api.get(n["href"]).json()["usageState"] == "busy"
    assert apidata.product_of(api, accepted)["realizingResource"][0]["id"] == n["id"]


def test_unchecked_resources_remain_documents(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    uid = str(uuid4())
    order = add(api, (offering, account), uid)
    assert order["state"] == "completed"
    product = apidata.product_of(api, order)
    assert product["realizingResource"] == refs(uid)
    assert api.patch(product["href"], {"realizingResource": []}).status_code == 200


def test_same_day_change_retains_empty_assignment(api, shop, db_conn):
    old, new = number(api), number(api, "12025550124")
    product = apidata.product_of(api, add(api, shop, old["id"]))
    assert modify(api, shop[1], product, new["id"], api.clock.today())["state"] == "completed"
    assert (
        db_conn.execute(
            "SELECT count(*) FROM number_assignment WHERE isempty(valid_for)"
        ).fetchone()[0]
        == 1
    )
    assert api.get(old["href"]).json()["allocationStatus"] == "available"


def test_assignment_emits_schema_valid_resource_events(api, shop, db_conn):
    n = number(api)
    subscribe(api, "TMF639")
    assert add(api, shop, n["id"])["state"] == "completed"
    events = stored(db_conn, "TMF639")
    assert {kind for kind, _ in events} == {
        "ResourceStateChangeEvent",
        "ResourceAttributeValueChangeEvent",
    }
    assert all(payload["event"]["resource"]["usageState"] == "busy" for _, payload in events)


def test_delete_unstarted_product_keeps_empty_number_history(api, shop, db_conn):
    n = number(api)
    product = apidata.product_of(
        api, add(api, shop, n["id"], timeline.TOMORROW + timedelta(days=3))
    )
    ended = apidata.order(api, shop[1], apidata.delete_item(product["id"]))
    assert ended["state"] == "completed"
    assert api.get(n["href"]).json()["allocationStatus"] == "available"
    assert db_conn.execute("SELECT isempty(valid_for) FROM number_assignment").fetchone()[0]
