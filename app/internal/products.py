"""The history of one product, under /internal/v1/product. Ours, not TMF (#458,
docs/decisions/frontend.md, "Product page"): TMF637 gives a product's order items but not
its status changes, offering changes or number and SIM assignments with their dates.

`GET /product/{id}/history` is one list of typed entries, newest effective date first,
paged with offset and limit. Read-only.
"""

from typing import Any

from fastapi import APIRouter, Request

from app.api.catalog import BASE as CATALOG
from app.api.common import parse_id
from app.api.deps import Tx
from app.api.errors import not_found
from app.api.inventory import order_href
from app.api.listing import page_response, parse_page, split_page
from app.api.resource_inventory import BASE as RESOURCES
from app.api.tmfjson import TmfJSONResponse
from app.repository import product_history, products
from app.repository._sql import Row

BASE = "/internal/v1/product"
router = APIRouter(prefix=BASE)


def entry_body(row: Row) -> dict[str, Any]:
    offering = row["offering_id"]
    number = row["number_id"]
    return {
        "kind": row["kind"],
        "date": row["day"],
        "endDate": row["end_day"],
        "status": row["status"],
        "cause": row["cause"],
        "productOffering": None
        if offering is None
        else {
            "id": str(offering),
            "href": f"{CATALOG}/productOffering/{offering}",
            "name": row["offering_name"],
        },
        "resource": None
        if number is None
        else {
            "id": str(number),
            "href": f"{RESOURCES}/resource/{number}",
            "kind": row["number_kind"],
            "value": row["number_value"],
            "iccid": row["iccid"],
        },
        "productOrder": {
            "id": str(row["product_order_id"]),
            "href": order_href(row["product_order_id"]),
        },
    }


@router.get("/{product_id}/history")
def list_product_history(product_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request)
    with tx() as conn:
        uid = parse_id("product", product_id)
        if products.get_product(conn, uid) is None:
            raise not_found("product", product_id)
        rows, more = split_page(product_history.history(conn, uid, offset, limit + 1), limit)
    return page_response(request, [entry_body(row) for row in rows], more, offset, limit)
