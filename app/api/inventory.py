"""TMF637 Product Inventory v5: product. Products are created and terminated only by
product orders (TMF622), and each product points back at the order items that did it.

The writes keep that rule by placing the order themselves, in the same transaction
(docs/decisions/tmf-api.md#product-writes-through-orders-2026-10-17): POST /product an
add order, PATCH terminationDate a delete order with that date, DELETE a delete order
effective today. An order that would be rejected is a 409 naming its reasons, and
nothing is stored. DELETE ends the product; it stays readable as billing history."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import (
    ProductOfferingRef,
    RatedBillingAccountRef,
    TmfModel,
    check_server_set,
    check_type,
    claim_id,
    created,
    from_billing_date,
    meta_body,
    meta_columns,
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
from app.api.prices import monthly_price, tax_on, total_rate
from app.api.tmfjson import TmfJSONResponse
from app.billing.core import cycle_period
from app.events import emit
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import billing, catalog, orders, parties, products
from app.repository._sql import Row

API = "TMF637"
BASE = "/tmf-api/productInventory/v5"
ORDERING_BASE = "/tmf-api/productOrderingManagement/v5"
router = APIRouter(prefix=BASE)

ATTRIBUTES = (
    "name",
    "status",
    "startDate",
    "terminationDate",
    "creationDate",
    "billingAccount",
    "productOffering",
    "productOrderItem",
    "productPrice",
    "productSpecification",
    "relatedParty",
    "orderDate",
)


def product_href(product_id: Any) -> str:
    return f"{BASE}/product/{product_id}"


def order_href(order_id: Any) -> str:
    return f"{ORDERING_BASE}/productOrder/{order_id}"


def _related_order_item(conn: psycopg.Connection, item_id: UUID) -> dict[str, Any]:
    item = orders.get_order_item(conn, item_id)
    assert item is not None  # foreign key
    return {
        "orderId": str(item["product_order_id"]),
        "orderHref": order_href(item["product_order_id"]),
        "orderItemId": item["item_id"],
        "orderItemAction": item["action"],
        "role": "creation" if item["action"] == "add" else "termination",
        "@type": "RelatedOrderItem",
        "@referredType": "ProductOrder",
    }


def product_body(conn: psycopg.Connection, row: Row, today: date) -> dict[str, Any]:
    account = parties.get_billing_account(conn, row["billing_account_id"])
    offering = catalog.get_product_offering(conn, row["product_offering_id"])
    assert account is not None and offering is not None  # foreign keys
    item_ids = [row["created_by_item_id"], row["terminated_by_item_id"]]
    terminated = row["termination_date"]
    # creationDate: the creating order's order_date - the injected clock of the request
    # that wrote the product (products are written only by orders), not created_at
    creating_item = orders.get_order_item(conn, row["created_by_item_id"])
    assert creating_item is not None  # foreign key
    creating_order = orders.get_order(conn, creating_item["product_order_id"])
    assert creating_order is not None  # foreign key
    spec_id = offering["product_specification_id"]  # None: an offering without one
    specification = None if spec_id is None else catalog.get_product_specification(conn, spec_id)
    customer = parties.get_customer(conn, account["customer_id"])
    assert customer is not None
    prices = []
    if row["status"] not in ("cancelled", "aborted") and (
        terminated is None or terminated > row["start_date"]
    ):
        day = min(today, terminated - timedelta(days=1)) if terminated else today
        priced_on = max(row["start_date"], cycle_period(day, account["bill_cycle_day"]).start)
        price = catalog.price_at(
            conn,
            product_offering_id=offering["id"],
            price_type="recurring",
            currency=account["currency"],
            at=from_billing_date(priced_on),
        )
        if price is not None:
            taxes = billing.price_taxes(price["details"])
            prices = [
                monthly_price(
                    price["amount"],
                    price["currency"],
                    resource_type="ProductPrice",
                    price_id=price["id"],
                    name=price["name"],
                    tax=tax_on(price["amount"], taxes),
                    tax_rate=total_rate(taxes),
                )
            ]
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": product_href(row["id"]),
            "name": offering["name"],
            "status": row["status"],
            "creationDate": utc(creating_order["order_date"]),
            "orderDate": utc(creating_order["order_date"]),
            "startDate": from_billing_date(row["start_date"]),
            # exclusive: the first day the product is no longer billed
            "terminationDate": from_billing_date(terminated) if terminated else None,
            "billingAccount": account_ref(account),
            "productPrice": prices,
            "productSpecification": None
            if specification is None
            else ref(
                "ProductSpecification",
                f"{CATALOG_BASE}/productSpecification/{specification['id']}",
                specification["id"],
                specification["name"],
            ),
            "relatedParty": [
                {
                    "role": "customer",
                    "@type": "RelatedPartyRefOrPartyRoleRef",
                    "partyOrPartyRole": ref(
                        "Customer",
                        f"/tmf-api/customer/v5/customer/{customer['id']}",
                        customer["id"],
                        customer["name"],
                        ref_type="PartyRoleRef",
                    ),
                }
            ],
            "productOffering": ref(
                "ProductOffering",
                f"{CATALOG_BASE}/productOffering/{offering['id']}",
                offering["id"],
                offering["name"],
            ),
            "productOrderItem": [
                _related_order_item(conn, item_id) for item_id in item_ids if item_id
            ],
            "@type": "Product",
        }
    ) | meta_body(row)


@router.get("/product")
def list_products(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
            "status": ("status", listing.as_str),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = products.list_products(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        today = clock.today()
        items = [product_body(conn, row, today) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/product/{product_id}")
def get_product(product_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = products.get_product(conn, parse_id("product", product_id))
        if row is None:
            raise not_found("product", product_id)
        body = product_body(conn, row, clock.today())
    return TmfJSONResponse(listing.select_fields(body, fields))


# --- writes: each one an order placed on the caller's behalf ---


class ProductCreate(TmfModel):
    billing_account: RatedBillingAccountRef
    product_offering: ProductOfferingRef
    start_date: AwareDatetime | None = None


class ProductPatch(TmfModel):
    termination_date: AwareDatetime | None = None


# Derived by the server (from the orders, the catalog, the account): a POST or PATCH may
# repeat them, never set them.
SERVER_SET = (
    "creationDate",
    "orderDate",
    "productOrderItem",
    "productPrice",
    "productSpecification",
    "relatedParty",
)
# What the orders decide: not written through TMF637 (yet; docs/decisions/tmf-api.md,
# "Following TMF", slice 9).
ORDER_DECIDED = ("status", "terminationDate", "name")


def _place(conn: psycopg.Connection, request: OrderRequest, *, now, today) -> Row:
    """Place the order; the stored order, or a 409 with its reasons if it would be
    rejected (then nothing is kept: the savepoint is rolled back)."""
    try:
        with conn.transaction():
            order_id = place_order(conn, request, now=now, today=today)
            order = orders.get_order(conn, order_id)
            assert order is not None
            if order["state"] == "rejected":
                reasons = "; ".join(
                    f"{m['code']}: {m['reason']}" for m in orders.error_messages(conn, order_id)
                )
                raise _Rejected(reasons)
    except _Rejected as rejected:
        raise conflict(f"the order for this change would be rejected - {rejected}") from None
    except OrderRefused as exc:
        raise unprocessable(str(exc)) from None
    from app.api.ordering import order_body  # lazily: that module imports this one

    emit.created(conn, "TMF622", "productOrder", lambda: order_body(conn, order), at=now)
    return order


class _Rejected(Exception):
    pass


def _end(conn, product_id: UUID, raw_id: str, ends: date | None, *, now, today) -> Row:
    """A delete order for the product: on `ends`, or today."""
    product = products.get_product(conn, product_id)
    if product is None:
        raise not_found("product", raw_id)
    if product["termination_date"] is not None:
        raise conflict(
            f"product {product_id} already ends on {product['termination_date']}"
            " (its status is " + product["status"] + "); cancel that order with"
            " cancelProductOrder to change it"
        )
    _place(
        conn,
        OrderRequest(
            billing_account_id=product["billing_account_id"],
            items=(ItemRequest("1", "delete", product_id=product_id),),
            requested_start=ends,
        ),
        now=now,
        today=today,
    )
    ended = products.get_product(conn, product_id)
    assert ended is not None
    return ended


@router.post("/product")
def create_product(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """An add order for one product of the offering on the account, from startDate
    (today if absent)."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Product")
    uid = take_id(body)
    server_set = take_server_set(body, (*SERVER_SET, "status"))
    data = parse(ProductCreate, body, read_only=ORDER_DECIDED)
    now, today = clock.now(), clock.today()
    start = to_billing_date(data.start_date) if data.start_date else None
    meta = meta_columns(data)
    chosen = products.NewProduct(
        id=uid, base_type=meta.get("base_type"), schema_location=meta.get("schema_location")
    )
    item = ItemRequest("1", "add", product_offering_id=data.product_offering.id, new_product=chosen)
    with tx() as conn:
        claim_id(conn, "product", uid)
        order = _place(
            conn,
            OrderRequest(
                billing_account_id=data.billing_account.id, items=(item,), requested_start=start
            ),
            now=now,
            today=today,
        )
        (placed,) = order["items"]
        new = product_body(conn, products.get_product(conn, placed["product_id"]), today)
        check_server_set(server_set, new)
    return created(listing.select_fields(new, fields))


@router.patch("/product/{product_id}")
def patch_product(
    product_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """terminationDate: a delete order with that date (the product's last day is the day
    before). Taking an end back is cancelProductOrder's job. Besides, only @baseType and
    @schemaLocation change."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Product")
    server_set = take_server_set(body, SERVER_SET)
    read_only = tuple(name for name in ORDER_DECIDED if name != "terminationDate")
    data = parse(
        ProductPatch, body, read_only=(*read_only, "billingAccount", "productOffering", "startDate")
    )
    ends = "termination_date" in data.model_fields_set
    if not ends and not meta_columns(data) and not server_set:
        raise bad_request("PATCH changes terminationDate, @baseType and @schemaLocation only")
    if ends and data.termination_date is None:
        raise unprocessable(
            "terminationDate cannot be removed: cancel the order that set it (cancelProductOrder)"
        )
    now, today = clock.now(), clock.today()
    with tx() as conn:
        uid = parse_id("product", product_id)
        current = products.get_product(conn, uid)
        if current is None:
            raise not_found("product", product_id)
        before = product_body(conn, current, today)
        check_server_set(server_set, before)
        if ends:
            row = _end(
                conn, uid, product_id, to_billing_date(data.termination_date), now=now, today=today
            )
            old = emit.before(conn, API, "product", lambda: product_body(conn, row, today))
        else:
            row, old = current, emit.before(conn, API, "product", lambda: before)
        patched = product_body(conn, with_meta(conn, "product", row, data), today)
        if meta_columns(data):
            emit.changed(conn, API, "product", old, patched, at=now)
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/product/{product_id}", status_code=204, response_class=Response)
def delete_product(product_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Ends the product today (one that has not started is cancelled): a delete order.
    The product stays readable - it is billing history."""
    listing.no_query(request)
    with tx() as conn:
        _end(
            conn,
            parse_id("product", product_id),
            product_id,
            None,
            now=clock.now(),
            today=clock.today(),
        )
    return Response(status_code=204)
