"""Usage of a line against its plan allowance, under /internal/v1/usage. Ours, not TMF
(TMF635 has the records; the allowance view is ours: docs/decisions/usage.md).

`GET /internal/v1/usage/summary?product.id=` : the plan period covering today (or `date`),
per kind (data bytes, voice seconds, sms messages) used, included (what the network was
last told, add-ons included), remaining, percent and the 80/100 marks reached, and whether
the line is throttled (the network said so, or data passed the cap). `payAsYouGo` is set
for a pay-as-you-go line: the account's balance, whether the line is barred on the network
(and the state of its last command) and how many of its records are still unrated.
"""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.common import money, utc
from app.api.deps import ClockDep, Tx
from app.api.errors import bad_request, not_found
from app.api.inventory import product_href
from app.api.tmfjson import TmfJSONResponse
from app.billing import usage_bar
from app.network import usage as rules
from app.repository import products

BASE = "/internal/v1/usage"
router = APIRouter(prefix=BASE)


@router.get("/summary")
def usage_summary(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    allowed = ("product.id", "date")
    for name in request.query_params:
        if name not in allowed:
            raise bad_request(f"unknown query parameter {name}")
    raw = request.query_params.get("product.id")
    if raw is None:
        raise bad_request("product.id is required")
    try:
        product_id = UUID(raw)
    except ValueError:
        raise bad_request("invalid value for product.id") from None
    day = clock.today()
    if "date" in request.query_params:
        try:
            day = date.fromisoformat(request.query_params["date"])
        except ValueError:
            raise bad_request("invalid value for date") from None
    with tx() as conn:
        product = products.get_product(conn, product_id)
        if product is None:
            raise not_found("product", raw)
        body: dict[str, Any] = rules.summary(conn, product_id, day)
        state = usage_bar.line_state(conn, product_id, day)
    body["payAsYouGo"] = None
    if state is not None:
        barring = state["barring"]
        body["payAsYouGo"] = {
            "balance": money(state["balance"], state["currency"]),
            "barred": state["barred"],
            "barring": None
            if barring is None
            else {**barring, "createdAt": utc(barring["createdAt"])},
            "unratedRecords": state["unratedRecords"],
        }
    return TmfJSONResponse({"product": {"id": raw, "href": product_href(raw)}, "date": day, **body})
