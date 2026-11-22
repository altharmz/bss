"""TMF637 product writes, each placed as a TMF622 order on the caller's behalf.

POST /product = an add order, PATCH terminationDate = a delete order with that date,
DELETE = a delete order effective today. Orders alone create or end products - these
are orders too, visible in TMF622. An order that would be rejected is a 409 with its
reasons and nothing is stored.
"""
# ruff: noqa: F811

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests import apidata
from tests.builders.api_order_cancel import CANCEL, cancel_body, rates, run, worker
from tests.builders.api_product_writes import (  # noqa: F401 - fixtures
    NOV1,
    OCT20,
    orders_of,
    product_body,
    shop,
    timeline_z,
)
from tests.timeline import iso, midnight

PRODUCT = apidata.PRODUCT
__all__ = ["worker"]


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


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_future_delete_dates(api, shop, worker, offset):
    offering, account = shop
    start = OCT20 + timedelta(days=1)
    end = OCT20 + timedelta(days=offset)
    product = api.post(
        PRODUCT, product_body(account, offering, startDate=iso(midnight(start)))
    ).json()
    placed = apidata.order(
        api, account, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(end))
    )
    assert placed["state"] == "completed"
    saved = api.get(product["href"]).json()
    assert saved["status"] == ("cancelled" if end <= start else "created")
    assert saved["terminationDate"] == timeline_z(max(start, end))
    if end > start:
        blocked = api.patch(product["href"], {"status": "suspended"})
        assert blocked.status_code == 409 and "productAlreadyEnded" in blocked.json()["message"]
    run(worker, api, start)
    assert api.get(product["href"]).json()["status"] == (
        "cancelled" if end <= start else "pendingTerminate"
    )
    run(worker, api, start + timedelta(days=1))
    assert api.get(product["href"]).json()["status"] == (
        "cancelled" if end <= start else "terminated"
    )
    assert sum((amount for _, amount in rates(api, product)), Decimal(0)) == (
        Decimal(0) if end <= start else Decimal("9.68")
    )


@pytest.mark.parametrize("catch_up", [False, True])
def test_post_future_product_with_end_and_cancel(api, shop, worker, catch_up):
    offering, account = shop
    end = NOV1 + timedelta(days=1)
    response = api.post(
        PRODUCT,
        product_body(
            account, offering, startDate=iso(midnight(NOV1)), terminationDate=iso(midnight(end))
        ),
    )
    assert response.status_code == 201, response.text
    product = response.json()
    assert product["status"] == "created" and product["terminationDate"] == timeline_z(end)
    if catch_up:
        run(worker, api, end)
        assert api.get(product["href"]).json()["status"] == "terminated"
    else:
        termination = next(r for r in product["productOrderItem"] if r["role"] == "termination")
        cancel = api.post(CANCEL, cancel_body({"id": termination["orderId"]}))
        assert cancel.status_code == 201, cancel.text
        restored = api.get(product["href"]).json()
        assert restored["status"] == "created" and "terminationDate" not in restored
        run(worker, api, NOV1)
        assert api.get(product["href"]).json()["status"] == "active"


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
    # status is the order's: repeating it is fine, another value a 422 (nothing stored)
    assert (
        api.post(PRODUCT, product_body(account, offering, status="terminated")).status_code == 422
    )
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
    # account and start date only repeat their value; status only suspends or resumes
    assert api.patch(product["href"], {"startDate": iso(midnight(NOV1))}).status_code == 409
    assert api.patch(product["href"], {"startDate": product["startDate"]}).status_code == 200
    assert api.patch(product["href"], {"status": "terminated"}).status_code == 422
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
    # orders are listed newest first (#312)
    assert [o["productOrderItem"][0]["action"] for o in orders_of(api, account)] == [
        "delete",
        "add",
        "delete",
        "add",
    ]


def test_mcp_product_writes_need_the_write_scope(api, mcp, shop):
    offering, account = shop
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "create_product" not in mcp.tools(reader)
    error, answer = mcp.result(writer, "create_product", {"body": product_body(account, offering)})
    assert not error and answer["status"] == 201
    error, answer = mcp.result(writer, "delete_product", {"product_id": answer["body"]["id"]})
    assert not error and answer == {"status": 204}
