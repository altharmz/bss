"""TMF622 Product Ordering Management v5: productOrder (GET list, GET by id, POST, DELETE
of a rejected order) and cancelProductOrder (GET list, GET by id, POST).

POST processes the order synchronously in the request's transaction (app/ordering). A
well-formed order that breaks a business rule is stored as `rejected` and answered with
201, its reasons in productOrderErrorMessage[]; only a malformed request, or one that
references something that does not exist, is a 4xx. No PATCH.

cancelProductOrder undoes a completed order before any of it takes effect
(app/ordering/cancel.py); one that cannot be cancelled is a 409 and nothing is stored,
so every stored cancellation is `done`.
"""

from decimal import Decimal
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime, Field

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import (
    BillingAccountRef,
    ProductOfferingRef,
    ProductRef,
    Ref,
    TmfModel,
    check_server_set,
    check_type,
    claim_id,
    created,
    from_billing_date,
    meta_body,
    parse,
    parse_id,
    ref,
    take_id,
    take_server_set,
    to_billing_date,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.inventory import ORDERING_BASE as BASE
from app.api.inventory import order_href, product_href
from app.api.prices import monthly_price
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.ordering import cancel as cancellation
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import catalog, orders, parties
from app.repository._sql import Row

router = APIRouter(prefix=BASE)
API = "TMF622"

ATTRIBUTES = (
    "state",
    "creationDate",
    "completionDate",
    "requestedStartDate",
    "billingAccount",
    "productOrderItem",
    "productOrderErrorMessage",
    "orderTotalPrice",
    "cancellationDate",
    "cancellationReason",
    "description",
    "category",
    "priority",
    "externalId",
)
# Set by processing (or the server): a POST or PATCH may repeat them, never set them.
SERVER_SET = (
    "state",
    "creationDate",
    "completionDate",
    "productOrderErrorMessage",
    "orderTotalPrice",
    "cancellationDate",
    "cancellationReason",
)
# What the order was placed with: fixed once it is processed (synchronously, at POST).
PLACED_WITH = ("requestedStartDate", "billingAccount", "productOrderItem")
CANCEL_ATTRIBUTES = (
    "productOrder",
    "creationDate",
    "requestedCancellationDate",
    "cancellationReason",
    "state",
    "effectiveCancellationDate",
)


class OrderItemIn(TmfModel):
    id: str = Field(min_length=1)
    action: Literal["add", "modify", "delete", "noChange"]
    quantity: Literal[1] = 1
    product_offering: ProductOfferingRef | None = None
    product: ProductRef | None = None
    billing_account: BillingAccountRef | None = None


class ExternalId(TmfModel):
    """TMF ExternalIdentifier: the order's id in another system."""

    id: str = Field(min_length=1)
    owner: str | None = Field(default=None, min_length=1)
    external_identifier_type: str | None = Field(default=None, min_length=1)


Priority = Literal["0", "1", "2", "3", "4"]


class OrderIn(TmfModel):
    billing_account: BillingAccountRef
    product_order_item: list[OrderItemIn]
    requested_start_date: AwareDatetime | None = None
    description: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1)
    priority: Priority | None = None
    external_id: list[ExternalId] | None = None


class OrderPatch(TmfModel):
    """Only what describes the order: processing is over (synchronous), so its state,
    items, dates and account are read-only. null removes an attribute."""

    description: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1)
    priority: Priority | None = None
    external_id: list[ExternalId] | None = None


def descriptive(data: OrderIn | OrderPatch) -> dict[str, Any]:
    """The descriptive columns the body sent (merge patch: null removes)."""
    changes = {}
    for name in orders.DESCRIPTIVE:
        if name not in data.model_fields_set:
            continue
        value = getattr(data, name)
        if name == "external_id":
            value = [e.model_dump(by_alias=True, exclude_none=True) for e in value or []]
        changes[name] = value
    return changes


def _external_body(entries: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    return [{**e, "@type": "ExternalIdentifier"} for e in entries] or None


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


def _item_body(conn: psycopg.Connection, row: Row, account: Row) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row["item_id"],
        "action": row["action"],
        "state": row["state"],
        "quantity": 1,
        # an item's account must be the order's, so it is the order's
        "billingAccount": account_ref(account),
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
            "productOrderItem": [_item_body(conn, item, account) for item in items],
            "productOrderErrorMessage": errors or None,
            "orderTotalPrice": total,
            "cancellationDate": utc(row["cancellation_date"]) if row["cancellation_date"] else None,
            "cancellationReason": row["cancellation_reason"],
            "description": row["description"],
            "category": row["category"],
            "priority": row["priority"],
            "externalId": _external_body(row["external_id"]),
            "@type": "ProductOrder",
        }
    ) | meta_body(row)


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
    uid = take_id(body)
    server_set = take_server_set(body, SERVER_SET)
    data = parse(OrderIn, body)
    for item in body.get("productOrderItem", []):
        check_type(item, "ProductOrderItem")
    requested = data.requested_start_date
    order_request = OrderRequest(
        billing_account_id=data.billing_account.id,
        items=tuple(_item_request(item) for item in data.product_order_item),
        requested_start=to_billing_date(requested) if requested else None,
        order_id=uid,
    )
    with tx() as conn:
        claim_id(conn, "product_order", uid)
        try:
            order_id = place_order(conn, order_request, now=clock.now(), today=clock.today())
        except OrderRefused as exc:
            raise unprocessable(str(exc)) from None
        attributes = descriptive(data)
        if attributes:
            orders.set_attributes(conn, order_id, attributes)
        row = orders.get_order(conn, order_id)
        assert row is not None
        new = order_body(conn, with_meta(conn, "product_order", row, data))
        check_server_set(server_set, new)
        # processed at once: one Create carrying the final state (completed or rejected)
        emit.created(conn, API, "productOrder", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/productOrder/{order_id}")
def patch_order(
    order_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """description, category, priority, externalId only (any order, any state)."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "ProductOrder")
    server_set = take_server_set(body, SERVER_SET)
    data = parse(OrderPatch, body, read_only=PLACED_WITH)
    with tx() as conn:
        uid = parse_id("productOrder", order_id)
        current = orders.lock_order(conn, uid)
        if current is None:
            raise not_found("productOrder", order_id)
        before = order_body(conn, current)
        check_server_set(server_set, before)
        old = emit.before(conn, API, "productOrder", lambda: before)
        changes = descriptive(data)
        if changes:
            orders.set_attributes(conn, uid, changes)
        row = orders.get_order(conn, uid)
        patched = order_body(conn, with_meta(conn, "product_order", row, data))
        emit.changed(conn, API, "productOrder", old, patched, at=clock.now())
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/productOrder/{order_id}", status_code=204, response_class=Response)
def delete_order(order_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a rejected order: it never executed, so nothing refers to it. Any other order
    is the history of its products (409); a completed one can be cancelled instead."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productOrder", order_id)
        row = orders.lock_order(conn, uid)
        if row is None:
            raise not_found("productOrder", order_id)
        if row["state"] != "rejected":
            raise conflict(
                f"productOrder {uid} is {row['state']}: only a rejected order can be deleted"
                " (a completed one can be cancelled with cancelProductOrder)"
            )
        emit.deleted(conn, API, "productOrder", lambda: order_body(conn, row), at=clock.now())
        orders.delete_rejected(conn, uid)
    return Response(status_code=204)


# --- cancelProductOrder ---


class ProductOrderRef(Ref):
    referred_type = "ProductOrder"


class CancelIn(TmfModel):
    product_order: ProductOrderRef
    requested_cancellation_date: AwareDatetime | None = None
    cancellation_reason: str | None = Field(default=None, min_length=1)


def cancel_href(uid: Any) -> str:
    return f"{BASE}/cancelProductOrder/{uid}"


def cancel_body(row: Row) -> dict[str, Any]:
    requested = row["requested_cancellation_date"]
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": cancel_href(row["id"]),
            "productOrder": ref(
                "ProductOrder",
                order_href(row["product_order_id"]),
                row["product_order_id"],
                ref_type="ProductOrderRef",
            ),
            "creationDate": utc(row["creation_date"]),
            "requestedCancellationDate": utc(requested) if requested else None,
            "cancellationReason": row["cancellation_reason"],
            "state": "done",  # a refused cancellation is a 409, never stored
            "effectiveCancellationDate": utc(row["creation_date"]),
            "@type": "CancelProductOrder",
        }
    ) | meta_body(row)


@router.get("/cancelProductOrder")
def list_cancellations(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={"productOrder.id": ("product_order_id", listing.as_uuid)},
        attributes=CANCEL_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = orders.list_cancellations(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([cancel_body(row) for row in rows], total, query)


@router.get("/cancelProductOrder/{cancel_id}")
def get_cancellation(cancel_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CANCEL_ATTRIBUTES)
    with tx() as conn:
        row = orders.get_cancellation(conn, parse_id("cancelProductOrder", cancel_id))
        if row is None:
            raise not_found("cancelProductOrder", cancel_id)
    return TmfJSONResponse(listing.select_fields(cancel_body(row), fields))


@router.post("/cancelProductOrder")
def cancel_order(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """Takes effect now: `requestedCancellationDate` may only be today (BILLING_TZ)."""
    fields = listing.retrieve_fields(request, CANCEL_ATTRIBUTES)
    check_type(body, "CancelProductOrder")
    uid = take_id(body)
    server_set = take_server_set(body, ("creationDate", "state", "effectiveCancellationDate"))
    data = parse(CancelIn, body)
    requested = data.requested_cancellation_date
    today = clock.today()
    if requested is not None and to_billing_date(requested) != today:
        raise unprocessable(
            f"requestedCancellationDate must be today ({today}): a cancellation takes effect"
            " when it is requested"
        )
    with tx() as conn:
        claim_id(conn, "cancel_product_order", uid)
        try:
            row = cancellation.cancel_order(
                conn,
                cancellation.CancelRequest(
                    order_id=data.product_order.id,
                    requested=requested,
                    reason=data.cancellation_reason,
                    id=uid,
                ),
                now=clock.now(),
                today=today,
            )
        except LookupError as exc:
            raise unprocessable(str(exc)) from None
        except cancellation.CancelRefused as exc:
            raise conflict(str(exc)) from None
        new = cancel_body(with_meta(conn, "cancel_product_order", row, data))
        check_server_set(server_set, new)
        emit.created(conn, API, "cancelProductOrder", new, at=row["creation_date"])
        order = orders.get_order(conn, row["product_order_id"])
        emit.emit(
            conn,
            API,
            "productOrder",
            "StateChange",
            lambda: order_body(conn, order),
            at=row["creation_date"],
        )
    return created(listing.select_fields(new, fields))
