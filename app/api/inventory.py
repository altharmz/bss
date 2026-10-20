"""TMF637 Product Inventory v5: product. Products are created, changed (offering,
status) and terminated only by product orders (TMF622), and each product points back at
the order items that did it.

The writes keep that rule by placing the order themselves, in the same transaction
(docs/decisions/tmf-api.md#product-writes-through-orders-2026-10-17): POST /product an
add order (and a delete order, with terminationDate), PATCH terminationDate a delete
order with that date, PATCH productOffering / status a modify order effective today,
DELETE a delete order effective today. An order that would be rejected is a 409 naming
its reasons, and nothing is stored. DELETE ends the product; it stays readable as
billing history.

A product's name (default: its offering's name at creation) and its descriptive
attributes (`DOCUMENTS`: stored as sent, checked against the spec) are its own: they
neither create nor end it, so PATCH changes them directly
(docs/decisions/tmf-api.md#the-last-gaps-2026-10-18)."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime, Field
from pydantic.alias_generators import to_camel

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import OfferingRef, check_version, offering_ref, specification_ref
from app.api.common import (
    RatedBillingAccountRef,
    check_server_set,
    check_type,
    claim_id,
    created,
    documents_model,
    from_billing_date,
    merge_documents,
    meta_body,
    meta_columns,
    parse_documented,
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
from app.billing.core import cycle_period, product_charge_period
from app.billing.model import MissingPrice
from app.config import settings
from app.events import emit
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import billing, catalog, orders, parties, products
from app.repository._sql import Row

API = "TMF637"
BASE = "/tmf-api/productInventory/v5"
ORDERING_BASE = "/tmf-api/productOrderingManagement/v5"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent in product.details, checked against the spec.
DOCUMENTS = (
    "description",
    "isBundle",
    "isCustomerVisible",
    "productSerialNumber",
    "agreementItem",
    "intent",
    "place",
    "product",
    "productCharacteristic",
    "productRelationship",
    "productTerm",
    "realizingResource",
    "realizingService",
)
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
    *DOCUMENTS,
)


def product_href(product_id: Any) -> str:
    return f"{BASE}/product/{product_id}"


def order_href(order_id: Any) -> str:
    return f"{ORDERING_BASE}/productOrder/{order_id}"


# RelatedOrderItem.role per item action: what the item did to the product
ROLES = {"add": "creation", "modify": "modification", "delete": "termination"}


def _related_order_item(conn: psycopg.Connection, item_id: UUID) -> dict[str, Any]:
    item = orders.get_order_item(conn, item_id)
    assert item is not None  # foreign key
    return {
        "orderId": str(item["product_order_id"]),
        "orderHref": order_href(item["product_order_id"]),
        "orderItemId": item["item_id"],
        "orderItemAction": item["action"],
        "role": ROLES[item["action"]],
        "@type": "RelatedOrderItem",
        "@referredType": "ProductOrder",
    }


def product_body(conn: psycopg.Connection, row: Row, today: date) -> dict[str, Any]:
    account = parties.get_billing_account(conn, row["billing_account_id"])
    # the offering valid today (on the start date for a product that starts later)
    changes = billing.offering_changes(conn, [row["id"]]).get(row["id"], ())
    core = billing.core_product(row, changes)
    offering = catalog.get_product_offering(conn, core.offering_on(max(today, row["start_date"])))
    assert account is not None and offering is not None  # foreign keys
    item_ids = [
        row["created_by_item_id"],
        *products.change_items(conn, row["id"]),
        *(c["changed_by_item_id"] for c in products.status_changes_of(conn, row["id"])),
        row["terminated_by_item_id"],
    ]
    if row.get("requires_msisdn"):
        number_items = conn.execute(
            """SELECT DISTINCT assigned_by_item_id
            FROM number_assignment WHERE product_id = %s AND cancelled_at IS NULL
            AND assigned_by_item_id <> %s ORDER BY assigned_by_item_id""",
            (row["id"], row["created_by_item_id"]),
        ).fetchall()
        item_ids.extend(item[0] for item in number_items)
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
        # the first day of the segment that bills `day` (billing-core.md)
        priced_on = max(
            row["start_date"],
            cycle_period(day, account["bill_cycle_day"]).start,
            *(c.day for c in changes if c.day <= day),
        )
        recurring = billing.load_recurring_prices(conn, list(core.offering_ids))
        try:
            priced_on = max(
                row["start_date"],
                product_charge_period(
                    core,
                    max(day, row["start_date"]),
                    recurring,
                    account["currency"],
                    account["bill_cycle_day"],
                    ZoneInfo(settings.billing_tz),
                ).start,
                *(c.day for c in changes if c.day <= day),
            )
        except MissingPrice:
            pass  # the lookup below returns no price, as before
        price = catalog.price_at(
            conn,
            product_offering_id=core.offering_on(priced_on),
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
                    recurring_period=price["recurring_period"],
                    recurring_period_length=price["recurring_period_length"],
                    tax=tax_on(price["amount"], taxes),
                    tax_rate=total_rate(taxes),
                    unit_of_measure=price["details"].get("unitOfMeasure"),
                )
            ]
    details = dict(row["details"])
    if row.get("requires_msisdn"):
        claim = conn.execute(
            """SELECT subscriber_number_id FROM number_assignment
            WHERE product_id = %s AND cancelled_at IS NULL AND valid_for @> %s::date""",
            (row["id"], max(today, row["start_date"])),
        ).fetchone()
        details["realizingResource"] = (
            []
            if claim is None
            else [
                {
                    "id": str(claim[0]),
                    "@type": "ResourceRef",
                    "@referredType": "LogicalResource",
                    "href": f"/tmf-api/resourceInventory/v5/resource/{claim[0]}",
                }
            ]
        )
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": product_href(row["id"]),
            "name": row["name"],
            "status": row["status"],
            **details,
            "creationDate": utc(creating_order["order_date"]),
            "orderDate": utc(creating_order["order_date"]),
            "startDate": from_billing_date(row["start_date"]),
            # exclusive: the first day the product is no longer billed
            "terminationDate": from_billing_date(terminated) if terminated else None,
            "billingAccount": account_ref(account),
            "productPrice": prices,
            "productSpecification": None
            if specification is None
            else specification_ref(specification),
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
            "productOffering": offering_ref(offering),
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
            "realizingResource.id": ("realizingResource.id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = products.list_products(
            conn, where=query.where, offset=query.offset, limit=query.limit, today=clock.today()
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


# --- writes: what creates, changes or ends a product is an order placed on the caller's
# behalf; its name and documents change directly ---

ProductDocuments = documents_model("ProductDocuments", API, DOCUMENTS)


class ProductCreate(ProductDocuments):  # type: ignore[misc,valid-type]
    billing_account: RatedBillingAccountRef
    product_offering: OfferingRef
    start_date: AwareDatetime | None = None
    termination_date: AwareDatetime | None = None
    name: str | None = Field(default=None, min_length=1)


class ProductPatch(ProductDocuments):  # type: ignore[misc,valid-type]
    name: str | None = Field(default=None, min_length=1)
    termination_date: AwareDatetime | None = None
    product_offering: OfferingRef | None = None
    status: str | None = None
    # a product's account and start date never change: only their current value
    billing_account: RatedBillingAccountRef | None = None
    start_date: AwareDatetime | None = None


# Each places its own order: one of them per PATCH.
PATCH_ORDERS = ("termination_date", "product_offering", "status")


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


def _check_offering_version(conn: psycopg.Connection, reference: OfferingRef) -> None:
    """A version sent must be the offering's current one (as in TMF620's references);
    an offering that does not exist is the order's to refuse."""
    offering = catalog.get_product_offering(conn, reference.id)
    if offering is not None:
        check_version(reference, offering, "productOffering")


@router.post("/product")
def create_product(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """An add order for one product of the offering on the account, from startDate
    (today if absent), named `name` (default: the offering's name) and holding the
    documents sent. With terminationDate, a delete order with that date follows in the
    same transaction: two ordinary orders, all or nothing."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Product")
    uid = take_id(body)
    server_set = take_server_set(body, (*SERVER_SET, "status"))
    data, documents = parse_documented(ProductCreate, body, DOCUMENTS, API, "Product_FVO")
    now, today = clock.now(), clock.today()
    start = to_billing_date(data.start_date) if data.start_date else None
    ends = to_billing_date(data.termination_date) if data.termination_date else None
    if ends is not None and ends <= (start or today):
        raise unprocessable("terminationDate must be after the product's start date")
    meta = meta_columns(data)
    chosen = products.NewProduct(
        id=uid,
        base_type=meta.get("base_type"),
        schema_location=meta.get("schema_location"),
        name=data.name,
        details={name: value for name, value in documents.items() if value is not None},
    )
    item = ItemRequest("1", "add", product_offering_id=data.product_offering.id, new_product=chosen)
    with tx() as conn:
        _check_offering_version(conn, data.product_offering)
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
        if ends is not None:
            _end(conn, placed["product_id"], str(placed["product_id"]), ends, now=now, today=today)
        new = product_body(conn, products.get_product(conn, placed["product_id"]), today)
        check_server_set(server_set, new)
    return created(listing.select_fields(new, fields))


def _patch_problems(data: ProductPatch) -> None:
    """What a PATCH body cannot ask for, before anything is read."""
    sent = data.model_fields_set
    placed = [name for name in PATCH_ORDERS if name in sent]
    if len(placed) > 1:
        raise unprocessable(
            "terminationDate, productOffering and status each place their own order:"
            " one of them per PATCH"
        )
    for name in ("name", "product_offering", "status", "billing_account", "start_date"):
        if name in sent and getattr(data, name) is None:
            raise unprocessable(f"{to_camel(name)} cannot be removed")
    if "termination_date" in sent and data.termination_date is None:
        raise unprocessable(
            "terminationDate cannot be removed: cancel the order that set it (cancelProductOrder)"
        )
    if data.status not in (None, "suspended", "active"):
        raise unprocessable(
            "status: PATCH suspends (suspended) or resumes (active) a product; it is created"
            " by an order and ended by terminationDate or DELETE"
        )


def _check_fixed(data: ProductPatch, current: Row) -> None:
    """billingAccount and startDate may only repeat the stored value."""
    sent = data.model_fields_set
    if "billing_account" in sent and data.billing_account.id != current["billing_account_id"]:
        raise conflict(
            "billingAccount: a product's account never changes (billing follows it); order"
            " a product on the other account and end this one"
        )
    if "start_date" in sent and to_billing_date(data.start_date) != current["start_date"]:
        raise conflict(
            "startDate: a start date moves by cancelling the order that created the product"
            " (cancelProductOrder) and placing a new one"
        )


@router.patch("/product/{product_id}")
def patch_product(
    product_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """name and the documents change directly (they neither create nor end a product).
    terminationDate places a delete order with that date (the product's last day is the
    day before; taking an end back is cancelProductOrder's job), productOffering a modify
    order changing the offering from today, status a modify order suspending (suspended)
    or resuming (active) it from today - one order per PATCH. billingAccount and
    startDate may only repeat their value."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Product")
    server_set = take_server_set(body, SERVER_SET)
    data, documents = parse_documented(ProductPatch, body, DOCUMENTS, API, "Product_MVO")
    _patch_problems(data)
    sent = data.model_fields_set
    if not sent and not server_set:
        raise bad_request("PATCH changes nothing")
    now, today = clock.now(), clock.today()
    with tx() as conn:
        uid = parse_id("product", product_id)
        current = products.get_product(conn, uid)
        if current is None:
            raise not_found("product", product_id)
        before = product_body(conn, current, today)
        if current.get("requires_msisdn") and "realizingResource" in documents:
            raise conflict("realizingResource: change subscriber numbers through a product order")
        check_server_set(server_set, before)
        _check_fixed(data, current)
        old = emit.before(conn, API, "product", lambda: before)
        row = current
        if documents or "name" in sent:
            row = products.describe(
                conn,
                uid,
                name=data.name if "name" in sent else current["name"],
                details=merge_documents(current["details"], documents),
            )
        row = with_meta(conn, "product", row, data)
        emit.changed(conn, API, "product", old, product_body(conn, row, today), at=now)
        if "termination_date" in sent:
            _end(
                conn, uid, product_id, to_billing_date(data.termination_date), now=now, today=today
            )
        elif "product_offering" in sent or "status" in sent:
            if data.product_offering is not None:
                _check_offering_version(conn, data.product_offering)
            item = ItemRequest(
                "1",
                "modify",
                product_id=uid,
                product_offering_id=data.product_offering.id if data.product_offering else None,
                product_status=data.status,
            )
            _place(
                conn,
                OrderRequest(billing_account_id=current["billing_account_id"], items=(item,)),
                now=now,
                today=today,
            )
        row = products.get_product(conn, uid)
        assert row is not None
        patched = product_body(conn, row, today)
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
