"""Event subscriptions (hubs) for operators, under /internal/v1/eventSubscription.
Ours, not TMF: TMF defines POST/DELETE /hub per API and no list.

Read only: the live hubs of every API, newest first (filter api), with the delivery
summary the outbox already stores. Creating and deleting stay on each API's /hub.
"""

from fastapi import APIRouter, Request

from app.api.common import utc
from app.api.deps import Tx
from app.api.errors import bad_request
from app.api.listing import parse_page
from app.api.tmfjson import TmfJSONResponse
from app.events.types import BASES
from app.repository import hubs
from app.repository._sql import Row

router = APIRouter(prefix="/internal/v1/eventSubscription")

API_NAMES = {
    "TMF620": "Product Catalog Management",
    "TMF622": "Product Ordering Management",
    "TMF629": "Customer Management",
    "TMF632": "Party Management",
    "TMF637": "Product Inventory Management",
    "TMF639": "Resource Inventory Management",
    "TMF654": "Prepay Balance Management",
    "TMF666": "Account Management",
    "TMF670": "Payment Method Management",
    "TMF676": "Payment Management",
    "TMF678": "Customer Bill Management",
}


def subscription_body(row: Row) -> dict:
    api = row["api"]
    return {
        "id": str(row["id"]),
        "api": {"id": api, "name": API_NAMES.get(api, api)},
        "hub": f"{BASES[api]}/hub/{row['id']}" if api in BASES else None,
        "callback": row["callback"],
        "query": row["query"],
        "eventTypes": list(row["event_types"] or []),
        "subscribedAt": utc(row["subscribed_at"]),
        "lastDelivery": None
        if row["last_state"] is None
        else {"state": row["last_state"], "at": utc(row["last_at"])},
        "failures": row["failures"],
    }


@router.get("")
def list_event_subscriptions(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("api",))
    given = request.query_params.getlist("api")
    if len(given) > 1:
        raise bad_request("api given more than once")
    api = given[0] if given else None
    if api is not None and api not in BASES:
        raise bad_request(f"api must be one of {', '.join(sorted(BASES))}")
    with tx() as conn:
        rows, total = hubs.page(conn, api=api, offset=offset, limit=limit)
    items = [subscription_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )
