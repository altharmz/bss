"""TMF637 product writes, each placed as a TMF622 order on the caller's behalf.

POST /product = an add order, PATCH terminationDate = a delete order with that date,
DELETE = a delete order effective today. Orders alone create or end products - these
are orders too, visible in TMF622. An order that would be rejected is a 409 with its
reasons and nothing is stored.
"""

from datetime import date

import pytest

from tests import apidata
from tests.test_billing_db import at
from tests.timeline import iso, midnight, z

PRODUCT = apidata.PRODUCT
OCT20, NOV1 = date(2026, 10, 20), date(2026, 11, 1)


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    api.clock.set(at(OCT20))
    return offering, account


def product_body(account, offering, **over):
    return {
        "@type": "Product",
        "billingAccount": {"id": account["id"]},
        "productOffering": {"id": offering["id"]},
        **over,
    }


def orders_of(api, account) -> list[dict]:
    return api.get(f"{apidata.ORDER}?billingAccount.id={account['id']}").json()


def test_post_places_an_add_order(api, shop):
    offering, account = shop
    response = api.post(PRODUCT, product_body(account, offering))
    assert response.status_code == 201, response.text
    product = response.json()
    assert response.headers["location"] == product["href"]
    assert product["status"] == "active" and product["startDate"] == timeline_z(OCT20)
    (order,) = orders_of(api, account)
    assert order["state"] == "completed"
    assert order["productOrderItem"][0]["action"] == "add"
    (creation,) = product["productOrderItem"]
    assert creation["role"] == "creation" and creation["orderId"] == order["id"]
    later = api.post(PRODUCT, product_body(account, offering, startDate=iso(midnight(NOV1)))).json()
    assert later["status"] == "created"


def timeline_z(day: date) -> str:
    return z(midnight(day))


def test_a_post_the_order_would_reject_is_a_409_and_stores_nothing(api, shop):
    offering, account = shop
    api.patch(offering["href"], {"isSellable": False})
    response = api.post(PRODUCT, product_body(account, offering))
    assert response.status_code == 409
    assert "offeringNotSellable" in response.json()["message"]
    assert orders_of(api, account) == []
    assert api.get(f"{PRODUCT}?billingAccount.id={account['id']}").json() == []


def test_post_validation(api, shop):
    offering, account = shop
    assert api.post(PRODUCT, product_body(account, offering, status="active")).status_code == 400
    assert api.post(PRODUCT, product_body(account, offering, colour="red")).status_code == 400
    assert api.post(PRODUCT, {"productOffering": {"id": offering["id"]}}).status_code == 422
    missing = product_body({"id": apidata.NIL}, offering)
    assert api.post(PRODUCT, missing).status_code == 422
    past = product_body(account, offering, startDate=iso(midnight(date(2026, 10, 19))))
    response = api.post(PRODUCT, past)
    assert response.status_code == 409 and "requestedStartDateInPast" in response.text


def test_patch_schedules_the_end_through_a_delete_order(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    ends = iso(midnight(date(2026, 12, 15)))
    response = api.patch(product["href"], {"terminationDate": ends})
    assert response.status_code == 200, response.text
    patched = response.json()
    assert patched["status"] == "pendingTerminate"
    assert patched["terminationDate"] == timeline_z(date(2026, 12, 15))
    roles = {r["role"]: r for r in patched["productOrderItem"]}
    assert roles["termination"]["orderItemAction"] == "delete"
    again = api.patch(product["href"], {"terminationDate": ends})
    assert again.status_code == 409 and "cancelProductOrder" in again.json()["message"]
    # the order it placed is an ordinary one: cancelling it takes the end back
    cancel = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {"productOrder": {"id": roles["termination"]["orderId"]}},
    )
    assert cancel.status_code == 201, cancel.text
    assert api.get(product["href"]).json()["status"] == "active"


def test_patch_validation(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    assert api.patch(product["href"], {"terminationDate": None}).status_code == 422
    assert api.patch(product["href"], {}).status_code == 400
    assert api.patch(product["href"], {"startDate": iso(midnight(NOV1))}).status_code == 400
    assert api.patch(product["href"], {"status": "terminated"}).status_code == 400
    assert (
        api.patch(f"{PRODUCT}/{apidata.NIL}", {"terminationDate": iso(midnight(NOV1))}).status_code
        == 404
    )


def test_delete_ends_the_product_today_and_it_stays_readable(api, shop):
    offering, account = shop
    product = api.post(PRODUCT, product_body(account, offering)).json()
    assert api.delete(f"{product['href']}?force=1").status_code == 400
    assert api.delete(product["href"]).status_code == 204
    ended = api.get(product["href"]).json()
    assert ended["status"] == "terminated" and ended["terminationDate"] == timeline_z(OCT20)
    again = api.delete(product["href"])
    assert again.status_code == 409 and "already ends" in again.json()["message"]
    future = api.post(PRODUCT, product_body(account, offering, startDate=iso(midnight(NOV1))))
    assert api.delete(future.json()["href"]).status_code == 204
    assert api.get(future.json()["href"]).json()["status"] == "cancelled"
    assert api.delete(f"{PRODUCT}/{apidata.NIL}").status_code == 404
    assert [o["productOrderItem"][0]["action"] for o in orders_of(api, account)] == [
        "add",
        "delete",
        "add",
        "delete",
    ]


def test_mcp_product_writes_need_the_write_scope(api, mcp, shop):
    offering, account = shop
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "create_product" not in mcp.tools(reader)
    error, answer = mcp.result(writer, "create_product", {"body": product_body(account, offering)})
    assert not error and answer["status"] == 201
    error, answer = mcp.result(writer, "delete_product", {"product_id": answer["body"]["id"]})
    assert not error and answer == {"status": 204}
