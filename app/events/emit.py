"""Emitting TMF events: called by every write, inside its own transaction.

`created`, `changed` and `deleted` turn a write into the API's listener events and store
them with their deliveries (app/repository/events.py) - only when some live hub wants
them, so a write nobody listens to costs one cheap query and builds no extra body.
Bodies are what the API answers for the resource (a delete: its body just before the
delete), so an event is as complete as a GET.

`changed` compares the body before and after a change: the state attribute differing
gives a <Resource>StateChangeEvent, any other attribute (lastUpdate aside) an
<Resource>AttributeValueChangeEvent; both, or neither when nothing changed.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.events.types import EVENT_TYPES
from app.repository import events as repo

# Nothing from app.api at import time: the API modules import this one, and importing
# the app.api package loads all of them (app/api/__init__.py).

Body = dict[str, Any]
Build = Callable[[], Body]
IGNORED = frozenset({"lastUpdate"})


def event_name(resource: str, kind: str) -> str:
    return f"{resource[0].upper()}{resource[1:]}{kind}Event"


def envelope(
    api: str, event_type: str, resource: str, body: Body, event_id: uuid.UUID, at: datetime
) -> Body:
    """The event as POSTed: TMF v5 Event (TMF676: its v4 notification structure)."""
    when = at.astimezone(UTC)  # as app.api.common.utc; tmfjson writes it as the API does
    if api == "TMF676":
        return {
            "eventId": str(event_id),
            "eventTime": when,
            "eventType": event_type,
            "event": {resource: body},
        }
    return {
        "id": str(event_id),
        "eventId": str(event_id),
        "eventTime": when,
        "timeOccurred": when,
        "eventType": event_type,
        "event": {resource: body},
        "@type": event_type,
    }


def _store(conn: psycopg.Connection, api: str, event_type: str, resource: str, body: Body, at):
    if event_type not in EVENT_TYPES[api]:
        raise ValueError(f"{api} has no {event_type}")  # a typo, not a runtime condition
    event_id = uuid.uuid4()
    repo.store(
        conn,
        event_id=event_id,
        api=api,
        event_type=event_type,
        resource=resource,
        resource_id=str(body["id"]),
        occurred_at=at,
        payload=envelope(api, event_type, resource, body, event_id, at),
    )


# --- changes that happen below the handlers (orders, settlement, the worker) ---
# The API bodies are imported lazily: these are called from app/ordering, app/billing and
# app/repository, which the API modules themselves import.


def bill(conn: psycopg.Connection, bill_id: Any, kind: str, *, at: datetime) -> None:
    """CustomerBill Create (once issued) or StateChange."""

    def build() -> Body:
        from app.api.bill import bill_body
        from app.repository import billing

        return bill_body(conn, billing.get_bill(conn, bill_id))

    emit(conn, "TMF678", "customerBill", kind, build, at=at)


def product(conn: psycopg.Connection, product_id: Any, kind: str, *, at: datetime, today) -> None:
    """Product Create or StateChange (orders, cancellations, the worker's flips)."""

    def build() -> Body:
        from app.api.inventory import product_body
        from app.repository import products

        return product_body(conn, products.get_product(conn, product_id), today)

    emit(conn, "TMF637", "product", kind, build, at=at)


def bill_demand(conn: psycopg.Connection, demand_id: Any, *, at: datetime) -> None:
    """CustomerBillOnDemand StateChange (the worker finished it)."""

    def build() -> Body:
        from app.api.on_demand import demand_body
        from app.repository._sql import fetch_one

        row = fetch_one(conn, "SELECT * FROM customer_bill_on_demand WHERE id = %s", (demand_id,))
        return demand_body(conn, row)

    emit(conn, "TMF678", "customerBillOnDemand", "StateChange", build, at=at)


def wanted(conn: psycopg.Connection, api: str, resource: str, *kinds: str) -> set[str]:
    return repo.wanted_by(conn, api, [event_name(resource, kind) for kind in kinds])


def emit(
    conn: psycopg.Connection, api: str, resource: str, kind: str, body: Body | Build, *, at
) -> None:
    """One event of `kind` (Create, Delete, StateChange, ...) about `resource`."""
    event_type = event_name(resource, kind)
    if not wanted(conn, api, resource, kind):
        return
    _store(conn, api, event_type, resource, body() if callable(body) else body, at)


def created(conn: psycopg.Connection, api: str, resource: str, body: Body | Build, *, at) -> None:
    emit(conn, api, resource, "Create", body, at=at)


def deleted(conn: psycopg.Connection, api: str, resource: str, body: Body | Build, *, at) -> None:
    emit(conn, api, resource, "Delete", body, at=at)


def before(conn: psycopg.Connection, api: str, resource: str, build: Build) -> Body | None:
    """The body before a change - built only if a hub wants change events of it."""
    if not wanted(conn, api, resource, "AttributeValueChange", "StateChange"):
        return None
    return build()


def changed(
    conn: psycopg.Connection,
    api: str,
    resource: str,
    old: Body | None,
    new: Body,
    *,
    at: datetime,
    state: str | None = None,
) -> None:
    """Events for a change from `old` (from `before`; None: nobody wanted them) to `new`."""
    if old is None:
        return
    keys = (set(old) | set(new)) - IGNORED - ({state} if state else set())
    events = []
    if state is not None and old.get(state) != new.get(state):
        events.append("StateChange")
    if any(old.get(k) != new.get(k) for k in keys):
        events.append("AttributeValueChange")
    wanted_now = wanted(conn, api, resource, *events) if events else set()
    for kind in events:
        if event_name(resource, kind) in wanted_now:
            _store(conn, api, event_name(resource, kind), resource, new, at)
