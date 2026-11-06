"""TMF622 Product Ordering Management v5: productOrder (GET list, GET by id, POST, PATCH,
DELETE of a rejected or draft order) and cancelProductOrder (GET list, GET by id, POST).

POST processes the order synchronously in the request's transaction (app/ordering). A
well-formed order that breaks a business rule is stored as `rejected` and answered with
201, its reasons in productOrderErrorMessage[]; only a malformed request, or one that
references something that does not exist, is a 4xx. With `requestedInitialState: draft`
the order is stored unprocessed: PATCH changes anything of it, and PATCH `state:
acknowledged` processes it as an order placed at that moment.

cancelProductOrder undoes a completed order before any of it takes effect
(app/ordering/cancel.py), or throws a draft away; one that cannot be cancelled is a 409
and nothing is stored, so every stored cancellation is `done`.
"""

import copy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime, Field

from app.api import listing, specschema
from app.api.account import account_ref, check_rated
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import (
    ProductOfferingRef,
    ProductRef,
    RatedBillingAccountRef,
    Ref,
    TmfModel,
    check_server_set,
    check_type,
    claim_id,
    created,
    documents_model,
    from_billing_date,
    merge_documents,
    meta_body,
    parse,
    parse_documented,
    parse_id,
    ref,
    same,
    take_id,
    take_server_set,
    to_billing_date,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import Problem, bad_request, conflict, not_found, unprocessable
from app.api.inventory import ORDERING_BASE as BASE
from app.api.inventory import order_href, product_href
from app.api.prices import monthly_price, tax_on, total_rate
from app.api.tmfjson import TmfJSONResponse
from app.billing.model import Tax
from app.events import emit
from app.network import device
from app.ordering import cancel as cancellation
from app.ordering import process
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import billing, catalog, idempotency, orders, parties, prepaid_commands
from app.repository._sql import Row

router = APIRouter(prefix=BASE)
API = "TMF622"

# Documents to us (stored as sent, checked against the spec, never read by processing).
ORDER_DOCUMENTS = (
    "agreement",
    "channel",
    "note",
    "notificationContact",
    "orderRelationship",
    "payment",
    "productOfferingQualification",
    "quote",
    "relatedParty",
    "requestedCompletionDate",
    "productOrderMilestone",
    "productOrderJeopardyAlert",
)
# ProductOrder_MVO also has expectedCompletionDate (not ProductOrder_FVO): PATCH only.
PATCH_DOCUMENTS = (*ORDER_DOCUMENTS, "expectedCompletionDate")
ITEM_DOCUMENTS = (
    "appointment",
    "itemTerm",
    "note",
    "payment",
    "productOfferingQualificationItem",
    "quoteItem",
    "qualification",
    "productOrderItemRelationship",
)
# Set by processing: an item sent may repeat them, never set them.
ITEM_SERVER_SET = ("state", "itemPrice", "itemTotalPrice")

ATTRIBUTES = (
    "state",
    "creationDate",
    "completionDate",
    "requestedStartDate",
    "requestedInitialState",
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
    *PATCH_DOCUMENTS,
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
# On PATCH, `state` submits a draft (to acknowledged); otherwise it is set by processing.
PATCH_SERVER_SET = tuple(name for name in SERVER_SET if name != "state")
# What the order is placed with: changeable while it is a draft, fixed once processed.
PLACED_WITH = ("requestedStartDate", "billingAccount", "productOrderItem")
CANCEL_ATTRIBUTES = (
    "productOrder",
    "creationDate",
    "requestedCancellationDate",
    "cancellationReason",
    "state",
    "effectiveCancellationDate",
)

ItemDocuments = documents_model("ProductOrderItemDocuments", API, ITEM_DOCUMENTS)


class ItemProduct(ProductRef):
    """The product an item names; a modify item's `status` suspends or resumes it
    (docs/decisions/tmf-api.md#suspension-2026-10-19)."""

    status: Literal["suspended", "active"] | None = None
    id: UUID | None = None
    realizing_resource: list[dict[str, Any]] | None = None
    # an add item may choose the kind of SIM instead of naming one (docs/decisions/sim.md)
    product_characteristic: list[dict[str, Any]] | None = None
    # an add-on pack's plan: [{relationshipType: addOn, id: <plan product>}] (add items only)
    product_relationship: list[dict[str, Any]] | None = None


class OrderItemIn(ItemDocuments):  # type: ignore[misc,valid-type]
    id: str = Field(min_length=1)
    action: Literal["add", "modify", "delete", "noChange"]
    quantity: Literal[1] = 1
    product_offering: ProductOfferingRef | None = None
    product: ItemProduct | None = None
    billing_account: RatedBillingAccountRef | None = None
    # nested items: processed like the others, listed after their parent
    product_order_item: list["OrderItemIn"] | None = None


OrderItemIn.model_rebuild()


class ExternalId(TmfModel):
    """TMF ExternalIdentifier: the order's id in another system."""

    id: str = Field(min_length=1)
    owner: str | None = Field(default=None, min_length=1)
    external_identifier_type: str | None = Field(default=None, min_length=1)


Priority = Literal["0", "1", "2", "3", "4"]
OrderDocuments = documents_model("ProductOrderDocuments", API, ORDER_DOCUMENTS)
PatchDocuments = documents_model("ProductOrderPatchDocuments", API, PATCH_DOCUMENTS)


class OrderIn(OrderDocuments):  # type: ignore[misc,valid-type]
    # optional, as in the spec: derived from the items when absent (app/ordering/process.py)
    billing_account: RatedBillingAccountRef | None = None
    product_order_item: list[OrderItemIn]
    requested_start_date: AwareDatetime | None = None
    requested_initial_state: Literal["acknowledged", "draft"] = "acknowledged"
    description: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1)
    priority: Priority | None = None
    external_id: list[ExternalId] | None = None


class OrderPatch(PatchDocuments):  # type: ignore[misc,valid-type]
    """What describes the order, any time; while it is a draft also what it is placed
    with (account, items, start date). null removes an attribute."""

    description: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1)
    priority: Priority | None = None
    external_id: list[ExternalId] | None = None
    billing_account: RatedBillingAccountRef | None = None
    product_order_item: list[OrderItemIn] | None = Field(default=None, min_length=1)
    requested_start_date: AwareDatetime | None = None


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


@dataclass(frozen=True)
class ItemSent:
    """What an item of the body carried besides what the model takes: its server-set
    attributes (compared with the result) and its documents (checked, normalized)."""

    id: Any
    server_set: dict[str, Any]
    documents: dict[str, Any]


def take_items(body: dict[str, Any], schema: str) -> tuple[list[ItemSent], list[Problem]]:
    """Every item of productOrderItem[], nested ones after their parent (as processing
    lists them): its server-set attributes taken out of the body, its documents checked
    against `schema` (ProductOrderItem_FVO or _MVO). Problems under their path."""
    sent: list[ItemSent] = []
    problems: list[Problem] = []

    def walk(items: Any, path: str) -> None:
        if not isinstance(items, list):
            return  # the model reports it
        for index, item in enumerate(items):
            where = f"{path}.{index}"
            if not isinstance(item, dict):
                continue
            check_type(item, "ProductOrderItem")
            server_set = {name: item.pop(name) for name in ITEM_SERVER_SET if name in item}
            documents = {
                name: item[name]
                for name in ITEM_DOCUMENTS
                if name in item and item[name] is not None
            }
            values, found = specschema.check(API, schema, documents)
            problems.extend((f"{where}.{loc}", message, unknown) for loc, message, unknown in found)
            sent.append(ItemSent(item.get("id"), server_set, values))
            walk(item.get("productOrderItem"), f"{where}.productOrderItem")

    walk(body.get("productOrderItem"), "productOrderItem")
    return sent, problems


def _flatten(
    items: list[OrderItemIn], parent: str | None = None
) -> list[tuple[OrderItemIn, str | None]]:
    flat = []
    for item in items:
        flat.append((item, parent))
        flat += _flatten(item.product_order_item or [], item.id)
    return flat


def _item_requests(items: list[OrderItemIn], sent: list[ItemSent]) -> tuple[ItemRequest, ...]:
    flat = _flatten(items)
    assert len(flat) == len(sent)  # both walk the body in the same order
    return tuple(
        _item_request(item, parent, extra.documents)
        for (item, parent), extra in zip(flat, sent, strict=True)
    )


def _check_characteristics(item: OrderItemIn) -> None:
    """productCharacteristic on an item: the SIM kind of an add (`simType`) and the device
    (`imei`) of an add or of a modify that changes nothing else."""
    chosen = item.product.product_characteristic
    names = [entry.get("name") for entry in chosen]
    if not chosen or any(name not in ("simType", "imei") for name in names):
        raise unprocessable(
            f"item {item.id}: the only supported characteristics are simType and imei"
        )
    if len(set(names)) != len(names):
        raise unprocessable(f"item {item.id}: each characteristic may be sent once")
    for entry in chosen:
        if entry["name"] == "simType":
            if item.action != "add":
                raise unprocessable(f"item {item.id}: simType is writable only on add items")
            if entry.get("value") not in ("physical", "esim"):
                raise unprocessable(f"item {item.id}: simType must be physical or esim")
        else:
            if item.action not in ("add", "modify"):
                raise unprocessable(f"item {item.id}: imei is writable only on add or modify items")
            if (why := device.problem(entry.get("value"))) is not None:
                raise unprocessable(f"item {item.id}: invalid imei: {why}")
    if item.action == "modify" and (
        item.product_offering is not None
        or item.product.status is not None
        or item.product.realizing_resource is not None
        or names != ["imei"]
    ):
        raise unprocessable(
            f"item {item.id}: a device change (imei) is a modify item of its own, changing"
            " nothing else"
        )


def _is_device_change(item: OrderItemIn) -> bool:
    return (
        item.action == "modify"
        and item.product is not None
        and item.product_offering is None
        and item.product.status is None
        and item.product.realizing_resource is None
        and any(e.get("name") == "imei" for e in item.product.product_characteristic or [])
    )


def _item_request(item: OrderItemIn, parent: str | None, documents: dict[str, Any]) -> ItemRequest:
    if item.action == "add":
        if item.product_offering is None:
            raise unprocessable(f"item {item.id}: an add item needs productOffering")
        if item.product is not None and (
            item.product.id is not None or item.product.status is not None
        ):
            raise unprocessable(f"item {item.id}: an add item cannot carry a product")
    elif item.action in ("delete", "noChange"):
        if item.product is None or item.product.id is None:
            raise unprocessable(f"item {item.id}: a {item.action} item needs product")
        if item.product_offering is not None:
            raise unprocessable(
                f"item {item.id}: a {item.action} item cannot carry productOffering"
            )
        if item.product.status is not None:
            raise unprocessable(
                f"item {item.id}: product.status is a modify item's (it suspends or resumes"
                " the product)"
            )
        if item.product.realizing_resource is not None:
            raise unprocessable("realizingResource is writable only on add or modify items")
    elif _is_device_change(item):
        if item.product.id is None:
            raise unprocessable(f"item {item.id}: a modify item needs product")
    elif (
        item.product is None
        or item.product.id is None
        or (item.product_offering is None and item.product.realizing_resource is None)
        == (item.product.status is None)
    ):
        raise unprocessable(
            f"item {item.id}: a modify item needs product and exactly one of productOffering,"
            " product.status (suspended or active), or product.realizingResource (a prepaid"
            " renewal may change productOffering and product.realizingResource together)"
        )
    if item.product and item.product.product_characteristic is not None:
        _check_characteristics(item)
    if item.product and item.product.product_relationship is not None and item.action != "add":
        raise unprocessable("productRelationship is writable only on add items")
    if item.product and (
        item.product.realizing_resource is not None
        or item.product.product_characteristic is not None
        or item.product.product_relationship is not None
    ):
        sent = {
            key: value
            for key, value in (
                ("realizingResource", item.product.realizing_resource),
                ("productCharacteristic", item.product.product_characteristic),
                ("productRelationship", item.product.product_relationship),
            )
            if value is not None
        }
        values, problems = specschema.check(API, "Product_FVO", sent)
        if problems:
            from app.api.errors import problems_error

            raise problems_error(problems)
        documents = {**documents, "product": values}
    return ItemRequest(
        item_id=item.id,
        action=item.action,
        product_offering_id=item.product_offering.id if item.product_offering else None,
        product_id=item.product.id if item.product else None,
        billing_account_id=item.billing_account.id if item.billing_account else None,
        parent_item_id=parent,
        details=documents or None,
        product_status=item.product.status if item.product else None,
    )


def _rated_refs(data: OrderIn | OrderPatch) -> list[RatedBillingAccountRef | None]:
    """The order's account reference and every item's (nested ones too)."""
    found = [data.billing_account]

    def walk(items: list[OrderItemIn] | None) -> None:
        for item in items or ():
            found.append(item.billing_account)
            walk(item.product_order_item)

    walk(data.product_order_item)
    return found


def _stored_request(conn: psycopg.Connection, order: Row) -> OrderRequest:
    """What a draft holds, as a request."""
    items = orders.items_of(conn, order["id"])
    item_ids = {item["id"]: item["item_id"] for item in items}
    return OrderRequest(
        billing_account_id=order["billing_account_id"],
        items=tuple(
            ItemRequest(
                item_id=item["item_id"],
                action=item["action"],
                product_offering_id=item["product_offering_id"],
                product_id=item["product_id"],
                parent_item_id=item_ids.get(item["parent_id"]),
                details=item["details"] or None,
                product_status=item["product_status"],
            )
            for item in items
        ),
        requested_start=order["requested_start_date"],
    )


def _check_items_server_set(sent: list[ItemSent], body: dict[str, Any]) -> None:
    """422 unless every server-set attribute an item sent equals the item's value."""
    answered: dict[Any, dict[str, Any]] = {}

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            answered[item["id"]] = item
            walk(item.get("productOrderItem", []))

    walk(body.get("productOrderItem", []))
    for item in sent:
        actual = answered.get(item.id, {})
        for name, value in item.server_set.items():
            if not same(value, actual.get(name)):
                raise unprocessable(
                    f"productOrderItem {item.id}: {name} is set by the server: send its"
                    " current value or omit it"
                )


def _quoted_taxes(conn: psycopg.Connection, item: Row, account: Row | None) -> tuple[Tax, ...]:
    """The tax items of the price an item was quoted at: they cannot change once the
    offering is ordered (a price's tax is part of its charge)."""
    if account is not None and account["rating_type"] == "prepaid":
        command = prepaid_commands.get(conn, item["product_order_id"])
        if command is not None:
            for quote in command["request"].get("intervals", []):
                if quote["product_id"] == str(item["product_id"]):
                    return tuple(Tax(t["category"], Decimal(t["rate"])) for t in quote["taxes"])
    price = catalog.get_product_offering_price(conn, item["quoted_price_id"])
    assert price is not None  # foreign key
    return billing.price_taxes(price["details"])


def _quoted_period(conn: psycopg.Connection, item: Row) -> dict[str, Any]:
    price = catalog.get_product_offering_price(conn, item["quoted_price_id"])
    assert price is not None
    return {
        "recurring_period": price["recurring_period"],
        "recurring_period_length": price["recurring_period_length"],
    }


def _quoted_unit(conn: psycopg.Connection, item: Row) -> dict[str, Any] | None:
    """The unitOfMeasure of the price an item was quoted at, if it has one."""
    price = catalog.get_product_offering_price(conn, item["quoted_price_id"])
    assert price is not None  # foreign key
    return price["details"].get("unitOfMeasure")


def _item_body(
    conn: psycopg.Connection, row: Row, account: Row | None, children: dict[Any, list[Row]]
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row["item_id"],
        "action": row["action"],
        "quantity": 1,
    }
    if row["state"] is not None:  # None: an item of a draft, not processed yet
        body["state"] = row["state"]
    if account is not None:
        # an item's account must be the order's, so it is the order's
        body["billingAccount"] = account_ref(account)
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
        if row["product_status"] is not None:
            body["product"]["status"] = row["product_status"]
    extra = dict(row["details"])
    if "product" in extra:
        body["product"] = {
            **extra.pop("product"),
            **body.get("product", {}),
            "@type": "Product",
        }
    body |= extra
    nested = children.get(row["id"], [])
    if nested:
        body["productOrderItem"] = [_item_body(conn, item, account, children) for item in nested]
    body["@type"] = "ProductOrderItem"
    if row["quoted_price_id"] is not None:
        taxes = _quoted_taxes(conn, row, account)
        price = monthly_price(
            row["quoted_amount"],
            row["quoted_currency"],
            resource_type="OrderPrice",
            price_id=row["quoted_price_id"],
            name=row["quoted_price_name"],
            tax=tax_on(row["quoted_amount"], taxes),
            tax_rate=total_rate(taxes),
            unit_of_measure=_quoted_unit(conn, row),
            billing_account=body.get("billingAccount"),
            **_quoted_period(conn, row),
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
    account = None
    if row["billing_account_id"] is not None:  # None: a draft that names none yet
        account = parties.get_billing_account(conn, row["billing_account_id"])
        assert account is not None  # foreign key
    items = orders.items_of(conn, row["id"])
    items_by_id = {item["id"]: item for item in items}
    children: dict[Any, list[Row]] = {}
    for item in items:
        children.setdefault(item["parent_id"], []).append(item)
    errors = [
        _error_body(message, items_by_id, row["id"])
        for message in orders.error_messages(conn, row["id"])
    ]
    completion = row["completion_date"]
    adds = [item for item in items if item["action"] == "add"]
    total = None
    if row["state"] == "completed" and adds and all(i["quoted_price_id"] for i in adds):
        assert account is not None  # a processed order has one
        taxes = [_quoted_taxes(conn, i, account) for i in adds]
        rates = {total_rate(t) for t in taxes}
        # one price for all items: the total comes from it, in its unit
        prices = {(i["quoted_price_id"], i["quoted_price_name"]) for i in adds}
        units = [_quoted_unit(conn, i) for i in adds]
        ((price_id, price_name),) = prices if len(prices) == 1 else ((None, None),)
        total = [
            monthly_price(
                sum((i["quoted_amount"] for i in adds), Decimal("0.00")),
                account["currency"],
                resource_type="OrderPrice",
                name="New subscriptions monthly total"
                if _quoted_period(conn, adds[0])
                == {"recurring_period": "monthly", "recurring_period_length": 1}
                else "New subscriptions recurring total",
                tax=sum(
                    (tax_on(i["quoted_amount"], t) for i, t in zip(adds, taxes, strict=True)),
                    Decimal("0.00"),
                ),
                tax_rate=rates.pop() if len(rates) == 1 else None,
                price_id=price_id,
                price_name=price_name,
                unit_of_measure=units[0] if all(u == units[0] for u in units) else None,
                billing_account=account_ref(account),
                **_quoted_period(conn, adds[0]),
            )
        ]
        # Different recurring intervals cannot be added into one recurring amount.
        if any(_quoted_period(conn, item) != _quoted_period(conn, adds[0]) for item in adds):
            total = [
                entry
                for item in adds
                for entry in _item_body(conn, item, account, children).get("itemTotalPrice", [])
            ]
    start = row["requested_start_date"]
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": order_href(row["id"]),
            "state": row["state"],
            "creationDate": utc(row["order_date"]),
            "completionDate": utc(completion) if completion else None,
            "requestedStartDate": from_billing_date(start) if start else None,
            "requestedInitialState": row["requested_initial_state"],
            "billingAccount": account_ref(account) if account else None,
            "productOrderItem": [
                _item_body(conn, item, account, children) for item in children.get(None, [])
            ],
            "productOrderErrorMessage": errors or None,
            "orderTotalPrice": total,
            "cancellationDate": utc(row["cancellation_date"]) if row["cancellation_date"] else None,
            "cancellationReason": row["cancellation_reason"],
            "description": row["description"],
            "category": row["category"],
            "priority": row["priority"],
            "externalId": _external_body(row["external_id"]),
            **row["details"],
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
    body_sent = copy.deepcopy(body)  # the hash covers what the client sent, before parsing
    check_type(body, "ProductOrder")
    if body.get("productOrderItem") == []:
        raise bad_request("an order needs at least one productOrderItem")
    uid = take_id(body)
    server_set = take_server_set(body, SERVER_SET)
    sent, found = take_items(body, "ProductOrderItem_FVO")
    data, documents = parse_documented(
        OrderIn, body, ORDER_DOCUMENTS, API, "ProductOrder_FVO", found
    )
    requested = data.requested_start_date
    order_request = OrderRequest(
        billing_account_id=data.billing_account.id if data.billing_account else None,
        items=_item_requests(data.product_order_item, sent),
        requested_start=to_billing_date(requested) if requested else None,
        order_id=uid,
        draft=data.requested_initial_state == "draft",
        details=merge_documents({}, documents) or None,
    )
    key = request.headers.get("idempotency-key")
    if key is not None:
        idempotency.check_key(key)
        operator = request.state.operator
        digest = idempotency.request_hash(list(request.query_params.multi_items()), body_sent)
    with tx() as conn:
        if key is not None:
            replay = idempotency.begin(conn, operator, "POST", request.url.path, key, digest)
            if replay is not None:
                return TmfJSONResponse(
                    replay.response,
                    status_code=replay.status_code,
                    headers={"Location": replay.location} if replay.location else None,
                )
        claim_id(conn, "product_order", uid)
        for reference in _rated_refs(data):
            check_rated(conn, reference)
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
        _check_items_server_set(sent, new)
        # processed at once: one Create carrying the final state (completed or rejected)
        emit.created(conn, API, "productOrder", new, at=clock.now())
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=clock.now(),
            )  # fmt: skip
    return created(selected)


@router.patch("/productOrder/{order_id}")
def patch_order(
    order_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """The descriptive attributes and documents of any order. A draft's account, items
    and start date too (each replaced whole), and `state: acknowledged` processes it.
    Once processed, those may only be repeated (409 otherwise)."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "ProductOrder")
    server_set = take_server_set(body, PATCH_SERVER_SET)
    state = body.pop("state", None)
    placed = {name: copy.deepcopy(body[name]) for name in PLACED_WITH if name in body}
    sent, found = take_items(body, "ProductOrderItem_MVO")
    data, documents = parse_documented(
        OrderPatch, body, PATCH_DOCUMENTS, API, "ProductOrder_MVO", found
    )
    with tx() as conn:
        uid = parse_id("productOrder", order_id)
        current = orders.lock_order(conn, uid)
        if current is None:
            raise not_found("productOrder", order_id)
        before = order_body(conn, current)
        check_server_set(server_set, before)
        old = emit.before(conn, API, "productOrder", lambda: before)
        draft = current["state"] == "draft"
        if draft and placed:
            _replace_draft(conn, current, data, sent)
        for name, value in placed.items():
            if not draft and not same(value, before.get(name)):
                raise conflict(
                    f"{name} cannot change: productOrder {uid} is {current['state']} (only"
                    " a draft's account, items and start date change)"
                )
        changes = descriptive(data)
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        if changes:
            orders.set_attributes(conn, uid, changes)
        if state is not None and state != current["state"]:
            if not draft or state != "acknowledged":
                raise unprocessable(
                    "state is set by processing: a draft is submitted with acknowledged,"
                    " an order is cancelled with cancelProductOrder"
                )
            _submit(conn, uid, now=clock.now(), today=clock.today())
        row = orders.get_order(conn, uid)
        patched = order_body(conn, with_meta(conn, "product_order", row, data))
        if draft and placed:
            _check_items_server_set(sent, patched)
        emit.changed(conn, API, "productOrder", old, patched, at=clock.now(), state="state")
    return TmfJSONResponse(listing.select_fields(patched, fields))


def _replace_draft(
    conn: psycopg.Connection, current: Row, data: OrderPatch, sent: list[ItemSent]
) -> None:
    for reference in _rated_refs(data):
        check_rated(conn, reference)
    held = _stored_request(conn, current)
    given = data.model_fields_set
    requested = data.requested_start_date
    request = OrderRequest(
        billing_account_id=(data.billing_account.id if data.billing_account else None)
        if "billing_account" in given
        else held.billing_account_id,
        items=_item_requests(data.product_order_item, sent)
        if data.product_order_item is not None
        else held.items,
        requested_start=(to_billing_date(requested) if requested else None)
        if "requested_start_date" in given
        else held.requested_start,
    )
    if "product_order_item" in given and data.product_order_item is None:
        raise unprocessable("productOrderItem cannot be null: an order needs at least one item")
    try:
        process.replace_draft(
            conn, current["id"], request, items=data.product_order_item is not None
        )
    except OrderRefused as exc:
        raise unprocessable(str(exc)) from None


def _submit(conn: psycopg.Connection, uid: Any, *, now: Any, today: Any) -> None:
    row = orders.get_order(conn, uid)
    assert row is not None  # locked
    try:
        process.submit_draft(conn, uid, _stored_request(conn, row), now=now, today=today)
    except OrderRefused as exc:
        raise unprocessable(str(exc)) from None


@router.delete("/productOrder/{order_id}", status_code=204, response_class=Response)
def delete_order(order_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a rejected or draft order: it never executed, so nothing refers to it. Any
    other order is the history of its products (409); a completed one can be cancelled
    instead."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productOrder", order_id)
        row = orders.lock_order(conn, uid)
        if row is None:
            raise not_found("productOrder", order_id)
        if row["state"] not in ("rejected", "draft"):
            raise conflict(
                f"productOrder {uid} is {row['state']}: only a rejected or draft order can be"
                " deleted (a completed one can be cancelled with cancelProductOrder)"
            )
        emit.deleted(conn, API, "productOrder", lambda: order_body(conn, row), at=clock.now())
        orders.delete_unexecuted(conn, uid)
        idempotency.tombstone(conn, order_href(uid), now=clock.now())
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
