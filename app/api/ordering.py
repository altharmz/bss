"""TMF622 Product Ordering Management v5: productOrder (GET list, GET by id, POST).

POST processes the order synchronously in the request's transaction (app/ordering). A
well-formed order that breaks a business rule is stored as `rejected` and answered with
201, its reasons in productOrderErrorMessage[]; only a malformed request, or one that
references something that does not exist, is a 4xx. No PATCH, DELETE or
cancelProductOrder.
"""

from decimal import Decimal
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request
from pydantic import AwareDatetime, Field

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import (
    BillingAccountRef,
    ProductOfferingRef,
    ProductRef,
    TmfModel,
    check_type,
    created,
    from_billing_date,
    parse,
    parse_id,
    ref,
    to_billing_date,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.inventory import ORDERING_BASE as BASE
from app.api.inventory import order_href, product_href
from app.api.prices import monthly_price
from app.api.tmfjson import TmfJSONResponse
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import catalog, orders, parties
from app.repository._sql import Row

router = APIRouter(prefix=BASE)

ATTRIBUTES = (
    "state",
    "creationDate",
    "completionDate",
    "requestedStartDate",
    "billingAccount",
    "productOrderItem",
    "productOrderErrorMessage",
    "orderTotalPrice",
)


class OrderItemIn(TmfModel):
    id: str = Field(min_length=1)
    action: Literal["add", "modify", "delete", "noChange"]
    quantity: Literal[1] = 1
    product_offering: ProductOfferingRef | None = None
    product: ProductRef | None = None
    billing_account: BillingAccountRef | None = None


class OrderIn(TmfModel):
    billing_account: BillingAccountRef
    product_order_item: list[OrderItemIn]
    requested_start_date: AwareDatetime | None = None


def _item_request(item: OrderItemIn) -> ItemRequest:
    if item.action == "add":
        if item.product_offering is None:
            raise unprocessable(f"item {item.id}: an add item needs productOffering")
        if item.product is not None:
            raise unprocessable(f"item {item.id}: an add item cannot carry a product")
    elif item.action == "delete":
        if item.product is None:
            raise unprocessable(f"item {item.id}: a delete item needs product")
        if item.product_offering is not None:
            raise unprocessable(f"item {item.id}: a delete item cannot carry productOffering")
    return ItemRequest(
        item_id=item.id,
        action=item.action,
        product_offering_id=item.product_offering.id if item.product_offering else None,
        # modify/noChange items keep no product: they are rejected anyway
        product_id=item.product.id if item.product and item.action == "delete" else None,
        billing_account_id=item.billing_account.id if item.billing_account else None,
    )


def _item_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row["item_id"],
        "action": row["action"],
        "state": row["state"],
        "quantity": 1,
    }
    if row["product_offering_id"] is not None:
        offering = catalog.get_product_offering(conn, row["product_offering_id"])
        assert offering is not None  # foreign key
        body["productOffering"] = ref(
            "ProductOffering",
            f"{CATALOG_BASE}/productOffering/{offering['id']}",
            offering["id"],
            offering["name"],
        )
    if row["product_id"] is not None:
        body["product"] = ref("Product", product_href(row["product_id"]), row["product_id"])
    body["@type"] = "ProductOrderItem"
    if row["quoted_price_id"] is not None:
        price = monthly_price(
            row["quoted_amount"],
            row["quoted_currency"],
            resource_type="OrderPrice",
            price_id=row["quoted_price_id"],
            name=row["quoted_price_name"],
        )
        body["itemPrice"] = [price]
        body["itemTotalPrice"] = [price]  # quantity is always 1
    return body


def _error_body(message: Row, items_by_id: dict[Any, Row], order_id: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": message["code"],
        "reason": message["reason"],
        "message": message["message"],
        "timestamp": utc(message["occurred_at"]),
    }
    item = items_by_id.get(message["product_order_item_id"])
    if item is not None:
        body["productOrderItem"] = [
            {
                "productOrderId": str(order_id),
                "productOrderItemId": item["item_id"],
                "@type": "ProductOrderItemRef",
                "@referredType": "ProductOrderItem",
            }
        ]
    body["@type"] = "ProductOrderErrorMessage"
    return without_nulls(body)


def order_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    items = orders.items_of(conn, row["id"])
    items_by_id = {item["id"]: item for item in items}
    errors = [
        _error_body(message, items_by_id, row["id"])
        for message in orders.error_messages(conn, row["id"])
    ]
    completion = row["completion_date"]
    adds = [item for item in items if item["action"] == "add"]
    total = None
    if row["state"] == "completed" and adds and all(i["quoted_price_id"] for i in adds):
        total = [
            monthly_price(
                sum((i["quoted_amount"] for i in adds), Decimal("0.00")),
                account["currency"],
                resource_type="OrderPrice",
                name="New subscriptions monthly total",
            )
        ]
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": order_href(row["id"]),
            "state": row["state"],
            "creationDate": utc(row["order_date"]),
            "completionDate": utc(completion) if completion else None,
            "requestedStartDate": from_billing_date(row["requested_start_date"]),
            "billingAccount": account_ref(account),
            "productOrderItem": [_item_body(conn, item) for item in items],
            "productOrderErrorMessage": errors or None,
            "orderTotalPrice": total,
            "@type": "ProductOrder",
        }
    )


@router.get("/productOrder")
def list_orders(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "state": ("state", listing.as_str),
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = orders.list_orders(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [order_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/productOrder/{order_id}")
def get_order(order_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = orders.get_order(conn, parse_id("productOrder", order_id))
        if row is None:
            raise not_found("productOrder", order_id)
        body = order_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post("/productOrder")
def create_order(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "ProductOrder")
    if body.get("productOrderItem") == []:
        raise bad_request("an order needs at least one productOrderItem")
    data = parse(
        OrderIn,
        body,
        read_only=(
            "state",
            "creationDate",
            "completionDate",
            "productOrderErrorMessage",
            "orderTotalPrice",
        ),
    )
    for item in body.get("productOrderItem", []):
        check_type(item, "ProductOrderItem")
    requested = data.requested_start_date
    order_request = OrderRequest(
        billing_account_id=data.billing_account.id,
        items=tuple(_item_request(item) for item in data.product_order_item),
        requested_start=to_billing_date(requested) if requested else None,
    )
    with tx() as conn:
        try:
            order_id = place_order(conn, order_request, now=clock.now(), today=clock.today())
        except OrderRefused as exc:
            raise unprocessable(str(exc)) from None
        row = orders.get_order(conn, order_id)
        assert row is not None
        new = order_body(conn, row)
    return created(listing.select_fields(new, fields))
