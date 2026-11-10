"""Devices of prepaid lines, under /internal/v1/device. Ours, not TMF
(docs/decisions/network.md#device-issue-194): TMF637 shows the current device as
characteristics; the history and the check are ours.

`GET /device?product.id=` is the line's devices, newest first (the open one has no
endedAt). `GET /device/check?imei=` asks the network about a device and stores nothing
(the admin "Check device" action); a network that cannot answer is `unknown`.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.common import utc
from app.api.deps import Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.inventory import product_href
from app.api.listing import page_response, parse_page, split_page
from app.api.tmfjson import TmfJSONResponse
from app.network import device
from app.ordering.devices import asked
from app.repository import devices as repo
from app.repository import products
from app.repository._sql import Row

BASE = "/internal/v1/device"
router = APIRouter(prefix=BASE)


def device_body(row: Row) -> dict[str, Any]:
    product = str(row["product_id"])
    return {
        "id": str(row["id"]),
        "product": {"id": product, "href": product_href(product)},
        "imei": row["imei"],
        "compatibility": row["compatibility"],
        "reasons": row["reasons"],
        "checkedAt": utc(row["checked_at"]),
        "startedAt": utc(row["started_at"]),
        "endedAt": None if row["ended_at"] is None else utc(row["ended_at"]),
        "recordedByItem": {"id": str(row["recorded_by_item_id"])},
    }


@router.get("")
def list_devices(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("product.id",))
    raw = request.query_params.get("product.id")
    if raw is None:
        raise bad_request("product.id is required")
    try:
        product_id = UUID(raw)
    except ValueError:
        raise bad_request("invalid value for product.id") from None
    with tx() as conn:
        if products.get_product(conn, product_id) is None:
            raise not_found("product", raw)
        rows, more = split_page(repo.history(conn, product_id)[offset:], limit)
    items = [device_body(row) for row in rows]
    return page_response(request, items, more, offset, limit)


@router.get("/check")
def check_device(request: Request) -> TmfJSONResponse:
    for name in request.query_params:
        if name != "imei":
            raise bad_request(f"unknown query parameter {name}")
    imei = request.query_params.get("imei")
    if imei is None:
        raise bad_request("imei is required")
    if (why := device.problem(imei)) is not None:
        raise unprocessable(f"invalid imei: {why}")
    verdict = asked(imei)
    return TmfJSONResponse(
        {"imei": imei, "result": verdict.result, "reasons": list(verdict.reasons)}
    )
