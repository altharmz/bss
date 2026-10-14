"""TMF637 Product Inventory v5: product. Read-only - products are created and terminated
only by product orders (TMF622), and each product points back at the order items that
did it."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import from_billing_date, parse_id, ref, utc, without_nulls
from app.api.deps import ClockDep, Tx
from app.api.errors import not_found
from app.api.prices import monthly_price
from app.api.tmfjson import TmfJSONResponse
from app.billing.core import cycle_period
from app.repository import catalog, orders, parties, products
from app.repository._sql import Row

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
    specification = catalog.get_product_specification(conn, offering["product_specification_id"])
    customer = parties.get_customer(conn, account["customer_id"])
    assert specification is not None and customer is not None
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
            prices = [
                monthly_price(
                    price["amount"],
                    price["currency"],
                    resource_type="ProductPrice",
                    price_id=price["id"],
                    name=price["name"],
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
            "productSpecification": ref(
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
                        f"/tmf-api/customerManagement/v5/customer/{customer['id']}",
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
    )


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
