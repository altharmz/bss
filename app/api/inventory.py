"""TMF637 Product Inventory v5: product. Read-only - products are created and terminated
only by product orders (TMF622), and each product points back at the order items that
did it."""

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import account_ref
from app.api.catalog import BASE as CATALOG_BASE
from app.api.common import from_billing_date, parse_id, ref, utc, without_nulls
from app.api.deps import Tx
from app.api.errors import not_found
from app.api.tmfjson import TmfJSONResponse
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


def product_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
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
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": product_href(row["id"]),
            "name": offering["name"],
            "status": row["status"],
            "creationDate": utc(creating_order["order_date"]),
            "startDate": from_billing_date(row["start_date"]),
            # exclusive: the first day the product is no longer billed
            "terminationDate": from_billing_date(terminated) if terminated else None,
            "billingAccount": account_ref(account),
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
def list_products(request: Request, tx: Tx) -> TmfJSONResponse:
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
        items = [product_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/product/{product_id}")
def get_product(product_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = products.get_product(conn, parse_id("product", product_id))
        if row is None:
            raise not_found("product", product_id)
        body = product_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))
