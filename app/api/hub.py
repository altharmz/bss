"""TMF hub: event subscriptions of every API (POST /hub, DELETE /hub/{id}; TMF629 also
GET /hub/{id}). docs/decisions/events.md.

A hub names a `callback` and, optionally, a `query` choosing event types:
`eventType=A` or `eventType=A,B`, names of the API's own listener events (anything else
is a 400). Without a query it receives every event of its API. The callback is checked
by app/events/callbacks.py (422 if refused). A hub id belongs to the API that created
it: another API's /hub answers 404 for it, as it does for a deleted one. TMF676 is v4:
its hub is an EventSubscription (no href, no @type).

Handlers are named <verb>_hub_<api> - the operationIds, and so the MCP tool names, are
the function names and must be unique.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.common import (
    TmfModel,
    check_type,
    created,
    meta_body,
    parse,
    parse_id,
    with_meta,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.events import callbacks
from app.events.types import BASES, EVENT_TYPES
from app.repository import events, hubs
from app.repository._sql import Row

V4 = frozenset({"TMF676"})
ATTRIBUTES = ("callback", "query")


class HubIn(TmfModel):
    callback: str = Field(min_length=1)
    query: str | None = Field(default=None, min_length=1)


def event_types(api: str, query: str | None) -> list[str] | None:
    """The event types a hub query names (None: every event of the API)."""
    if query is None:
        return None
    name, sep, value = query.partition("=")
    if name != "eventType" or not sep or not value or "&" in value:
        raise bad_request(
            "query: only eventType=<type>[,<type>...] is supported, for example"
            f" eventType={min(EVENT_TYPES[api])}"
        )
    names = value.split(",")
    unknown = sorted(set(names) - EVENT_TYPES[api])
    if unknown or len(set(names)) != len(names):
        problem = f"unknown to {api}: {', '.join(unknown)}" if unknown else "duplicate names"
        raise bad_request(f"query: eventType {problem}")
    return names


def hub_body(api: str, row: Row) -> dict[str, Any]:
    body: dict[str, Any] = {"id": str(row["id"])}
    if api not in V4:
        body["href"] = f"{BASES[api]}/hub/{row['id']}"
    body["callback"] = row["callback"]
    if row["query"] is not None:
        body["query"] = row["query"]
    if api not in V4:
        body["@type"] = "Hub"
    return body | meta_body(row)


def _checked(body: dict[str, Any], request: Request) -> None:
    listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Hub", "EventSubscriptionInput")


def _create(api: str, data: HubIn, tx: Tx, clock) -> TmfJSONResponse:
    types = event_types(api, data.query)
    try:
        callbacks.check(data.callback)
    except callbacks.CallbackRefused as exc:
        raise unprocessable(str(exc)) from None
    with tx() as conn:
        row = hubs.create(
            conn,
            api=api,
            callback=data.callback,
            query=data.query,
            event_types=types,
            at=clock.now(),
        )
        row = with_meta(conn, "event_subscription", row, data)
    hub = hub_body(api, row)
    if api in V4:  # no href to point a Location at
        return TmfJSONResponse(hub, status_code=201)
    return created(hub)


def _get(api: str, hub_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = hubs.get_live(conn, api, _hub_id(hub_id))
        if row is None:
            raise not_found("hub", hub_id)
    return TmfJSONResponse(listing.select_fields(hub_body(api, row), fields))


def _delete(api: str, hub_id: str, request: Request, tx: Tx, clock) -> Response:
    """Pending deliveries to it are dropped (they name a deleted subscription)."""
    listing.no_query(request)
    with tx() as conn:
        uid = _hub_id(hub_id)
        if hubs.get_live(conn, api, uid, lock=True) is None:
            raise not_found("hub", hub_id)
        now = clock.now()
        hubs.mark_deleted(conn, uid, now)
        events.drop_pending(conn, uid, now)
    return Response(status_code=204)


def _hub_id(raw: str) -> UUID:
    return parse_id("hub", raw)


# (api, short name in the handler names); only TMF629's spec has GET /hub/{id}
HUB_APIS = (
    ("TMF620", "catalog"),
    ("TMF622", "ordering"),
    ("TMF629", "customer"),
    ("TMF632", "party"),
    ("TMF637", "inventory"),
    ("TMF666", "account"),
    ("TMF676", "payment"),
    ("TMF678", "bill"),
)
_routers = {api: APIRouter(prefix=BASES[api]) for api, _ in HUB_APIS}
ROUTERS = list(_routers.values())
READ_ONLY = ("id", "href")
HUB = {"response_class": TmfJSONResponse, "status_code": 201}
GONE = {"response_class": Response, "status_code": 204}


# --- one handler per API and operation (the function name is the operationId) ---


@_routers["TMF620"].post("/hub", **HUB)
def create_hub_catalog(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF620", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF620"].delete("/hub/{hub_id}", **GONE)
def delete_hub_catalog(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF620", hub_id, request, tx, clock)


@_routers["TMF622"].post("/hub", **HUB)
def create_hub_ordering(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF622", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF622"].delete("/hub/{hub_id}", **GONE)
def delete_hub_ordering(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF622", hub_id, request, tx, clock)


@_routers["TMF629"].post("/hub", **HUB)
def create_hub_customer(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF629", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF629"].get("/hub/{hub_id}")
def get_hub_customer(hub_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get("TMF629", hub_id, request, tx)


@_routers["TMF629"].delete("/hub/{hub_id}", **GONE)
def delete_hub_customer(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF629", hub_id, request, tx, clock)


@_routers["TMF632"].post("/hub", **HUB)
def create_hub_party(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF632", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF632"].delete("/hub/{hub_id}", **GONE)
def delete_hub_party(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF632", hub_id, request, tx, clock)


@_routers["TMF637"].post("/hub", **HUB)
def create_hub_inventory(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF637", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF637"].delete("/hub/{hub_id}", **GONE)
def delete_hub_inventory(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF637", hub_id, request, tx, clock)


@_routers["TMF666"].post("/hub", **HUB)
def create_hub_account(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF666", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF666"].delete("/hub/{hub_id}", **GONE)
def delete_hub_account(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF666", hub_id, request, tx, clock)


@_routers["TMF676"].post("/hub", **HUB)
def create_hub_payment(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF676", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF676"].delete("/hub/{hub_id}", **GONE)
def delete_hub_payment(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF676", hub_id, request, tx, clock)


@_routers["TMF678"].post("/hub", **HUB)
def create_hub_bill(body: JsonBody, request: Request, tx: Tx, clock: ClockDep):
    _checked(body, request)
    return _create("TMF678", parse(HubIn, body, read_only=READ_ONLY), tx, clock)


@_routers["TMF678"].delete("/hub/{hub_id}", **GONE)
def delete_hub_bill(hub_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete("TMF678", hub_id, request, tx, clock)
