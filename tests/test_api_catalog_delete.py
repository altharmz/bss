"""TMF620 DELETE: only what nothing references, so nothing ever ordered changes.

D1 (no DELETE anywhere) is narrowed to the catalog: specifications, offerings, prices,
categories and catalogs can be deleted while unreferenced - typically entries created by
mistake. An offering that was ever ordered keeps itself and its prices (409); retiring is
the way out. Concurrency: orders hold their offerings FOR SHARE and new offerings their
specification, so a delete never races a write that would reference what it removes.
"""

import threading
from datetime import timedelta
from uuid import UUID

import psycopg

from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import catalog
from tests import apidata, timeline
from tests.apidata import ORDER
from tests.test_api_catalog import (
    BASE,
    NIL,
    OFFERING,
    SPEC,
    make_offering,
    make_price,
    make_spec,
    offering_body,
    price_refs,
)

CATEGORY = f"{BASE}/category"
CATALOG = f"{BASE}/productCatalog"


def gone(api, href: str) -> None:
    assert api.get(href).status_code == 404
    assert api.delete(href).status_code == 404


def refused(api, href: str, *expected: str) -> str:
    response = api.delete(href)
    assert response.status_code == 409, response.text
    message = response.json()["message"]
    for text in expected:
        assert text in message, message
    assert api.get(href).status_code == 200
    return message


def test_unreferenced_entries_are_deleted(api):
    spec = make_spec(api)
    price = make_price(api)
    offering = make_offering(api, spec)
    root = api.post(CATEGORY, {"name": "Internet"}).json()
    home = api.post(CATALOG, {"name": "Home"}).json()
    for entry in (home, root, price, offering, spec):
        response = api.delete(entry["href"])
        assert response.status_code == 204, response.text
        gone(api, entry["href"])
    for path in (SPEC, OFFERING, f"{BASE}/productOfferingPrice", CATEGORY, CATALOG):
        assert api.get(path).json() == []
        assert api.delete(f"{path}/{NIL}").status_code == 404
        assert api.delete(f"{path}/invalid").status_code == 404
        assert api.delete(path).status_code == 405


def test_delete_takes_no_query_parameters(api):
    spec = make_spec(api)
    for query in ("fields=name", "force=true"):
        assert api.delete(f"{spec['href']}?{query}").status_code == 400
    assert api.get(spec["href"]).status_code == 200


def test_a_specification_in_use_stays(api):
    spec = make_spec(api)
    offering = make_offering(api, spec)
    refused(api, spec["href"], "1 productOffering", "delete those offerings first")
    assert api.delete(offering["href"]).status_code == 204
    assert api.delete(spec["href"]).status_code == 204


def test_an_unordered_offering_goes_after_its_prices(api):
    price = make_price(api)
    offering = make_offering(api, productOfferingPrice=price_refs(price))
    fiber = api.post(CATEGORY, {"name": "Fiber", "productOffering": [{"id": offering["id"]}]})
    refused(api, offering["href"], "1 productOfferingPrice", "delete its prices first")
    # never ordered: an attached price can go, and the offering no longer lists it
    assert api.delete(price["href"]).status_code == 204
    assert api.get(offering["href"]).json()["productOfferingPrice"] == []
    # its category membership goes with the offering; the category stays
    assert api.delete(offering["href"]).status_code == 204
    assert api.get(fiber.json()["href"]).json()["productOffering"] == []


def test_an_ordered_offering_and_its_prices_stay(api):
    offering = apidata.sellable_offering(api)
    price = api.get(offering["productOfferingPrice"][0]["href"]).json()
    api.clock.set(timeline.START + timedelta(hours=9))
    acc = apidata.account(api)
    apidata.order(api, acc, apidata.add_item(offering))
    before = [api.get(e["href"]).json() for e in (offering, price)]
    refused(api, offering["href"], "1 product,", "1 productOrderItem", "retire it instead")
    refused(api, price["href"], "1 product of its offering", "end its validFor instead")
    spec = offering["productSpecification"]
    refused(api, f"{SPEC}/{spec['id']}", "1 productOffering")
    assert [api.get(e["href"]).json() for e in (offering, price)] == before


def test_a_rejected_order_also_keeps_the_offering(api):
    """Conservative: any order item refers to the offering, even a rejected one."""
    offering = apidata.sellable_offering(api)
    api.patch(offering["href"], {"isSellable": False})
    api.clock.set(timeline.START + timedelta(hours=9))
    placed = apidata.order(api, apidata.account(api), apidata.add_item(offering))
    assert placed["state"] == "rejected"
    refused(api, offering["href"], "1 productOrderItem")
    refused(api, offering["productOfferingPrice"][0]["href"], "1 productOrderItem of its")


def test_category_and_catalog_rules(api):
    offering = make_offering(api)
    root = api.post(CATEGORY, {"name": "Internet"}).json()
    child = api.post(
        CATEGORY,
        {
            "name": "Fiber",
            "parent": {"id": root["id"]},
            "productOffering": [{"id": offering["id"]}],
        },
    ).json()
    home = api.post(CATALOG, {"name": "Home", "category": [{"id": root["id"]}]}).json()
    refused(api, root["href"], "1 subCategory", "1 productCatalog", "remove them from it first")
    refused(api, child["href"], "1 productOffering")
    # a catalog is never referenced; its root categories stay
    assert api.delete(home["href"]).status_code == 204
    assert api.get(root["href"]).status_code == 200
    assert api.patch(child["href"], {"productOffering": []}).status_code == 200
    assert api.delete(child["href"]).status_code == 204
    assert api.get(root["href"]).json()["subCategory"] == []
    assert api.delete(root["href"]).status_code == 204
    assert api.get(offering["href"]).json()["category"] == []


def _waits(target) -> dict:
    """Start `target` on a thread; it must still be blocked two seconds later."""
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(response=target()))
    thread.start()
    thread.join(timeout=2)
    assert thread.is_alive(), "it did not wait for the open transaction"
    result["thread"] = thread
    return result


def _finish(result: dict):
    result["thread"].join(timeout=10)
    assert not result["thread"].is_alive()
    return result["response"]


def test_a_price_delete_waits_for_an_order_in_flight(api, migrated_db_url):
    offering = apidata.sellable_offering(api)
    price_href = offering["productOfferingPrice"][0]["href"]
    acc = apidata.account(api)
    now = timeline.START + timedelta(hours=9)
    api.clock.set(now)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            place_order(
                first,
                OrderRequest(
                    billing_account_id=UUID(acc["id"]),
                    items=(ItemRequest("1", "add", product_offering_id=UUID(offering["id"])),),
                ),
                now=now,
                today=timeline.TOMORROW,
            )
            result = _waits(lambda: api.delete(price_href))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 409
    assert "1 productOrderItem of its offering" in response.json()["message"]


def test_an_order_waits_for_a_price_delete_in_flight(api, migrated_db_url):
    """Without the order's FOR SHARE lock it validates the price the delete removes, then
    fails on the quote's foreign key (500) instead of being rejected."""
    offering = apidata.sellable_offering(api)
    price_id = UUID(offering["productOfferingPrice"][0]["id"])
    acc = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            catalog.lock_offering(first, UUID(offering["id"]))
            first.execute("DELETE FROM product_offering_price WHERE id = %s", (price_id,))
            result = _waits(
                lambda: api.post(ORDER, apidata.order_body(acc, apidata.add_item(offering)))
            )
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "rejected"
    assert "noRecurringPrice" in {m["code"] for m in order["productOrderErrorMessage"]}


def test_a_new_offering_waits_for_a_specification_delete_in_flight(api, migrated_db_url):
    spec = make_spec(api)
    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            catalog.lock_product_specification(first, UUID(spec["id"]))
            first.execute("DELETE FROM product_specification WHERE id = %s", (UUID(spec["id"]),))
            result = _waits(lambda: api.post(OFFERING, offering_body(spec)))
        response = _finish(result)
    finally:
        first.close()
    assert response.status_code == 422, response.text
    assert "does not exist" in response.json()["message"]


def test_mcp_deletes_with_the_write_scope_only(api, mcp):
    spec = make_spec(api)
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "delete_specification" not in mcp.tools(reader)
    response = mcp.call(reader, "delete_specification", {"spec_id": spec["id"]})
    assert response.json()["error"] == "insufficient_scope"
    assert api.get(spec["href"]).status_code == 200
    error, answer = mcp.result(writer, "delete_specification", {"spec_id": spec["id"]})
    assert not error and answer == {"status": 204}
    gone(api, spec["href"])
