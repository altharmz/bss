"""Marketing consent and message resend for operators, under /internal/v1/notification.
Ours, not TMF: TMF681 has the message log, consent and resend are ours
(docs/decisions/notifications.md).

`GET /consent?customer.id=` the customer's current marketing consent per channel and its
history; `POST /consent` records an opt-in or opt-out (append-only; no row means not opted in;
transactional messages need no consent). `POST /message/{id}/resend` queues a copy of a
message that is no longer open (sent, failed or cancelled); the worker sends it and the
consent and quiet-hours rules apply again.
"""

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field

from app.api.common import Ref, TmfModel, billing_tz, created, parse, parse_id, utc
from app.api.communication import message_body, message_href
from app.api.customer import customer_href
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query, operator_of
from app.notify import service
from app.repository import notifications as repo
from app.repository._sql import Row, fetch_one

BASE = "/internal/v1/notification"
router = APIRouter(prefix=BASE)


class CustomerRef(Ref):
    referred_type = "Customer"


class ConsentIn(TmfModel):
    customer: CustomerRef
    channel: str
    opted_in: bool
    source: str = Field(min_length=1, max_length=200)


def _entry(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "channel": row["channel"],
        "category": row["category"],
        "optedIn": row["opted_in"],
        "source": row["source"],
        "recordedBy": row["recorded_by"],
        "recordedAt": utc(row["recorded_at"]),
    }


def consent_body(conn: psycopg.Connection, customer_id: UUID) -> dict[str, Any]:
    current = repo.consent_current(conn, customer_id)
    return {
        "customer": {"id": str(customer_id), "href": customer_href(customer_id)},
        "marketing": {
            channel: None if channel not in current else _entry(current[channel])
            for channel in repo.CHANNELS
        },
        "history": [_entry(row) for row in repo.consent_history(conn, customer_id)],
    }


def _customer(conn: psycopg.Connection, customer_id: UUID) -> None:
    if fetch_one(conn, "SELECT 1 FROM customer WHERE id = %s", (customer_id,)) is None:
        raise not_found("customer", str(customer_id))


@router.get("/consent")
def get_consent(request: Request, tx: Tx) -> TmfJSONResponse:
    for name in request.query_params:
        if name != "customer.id":
            raise bad_request(f"unknown query parameter {name}")
    raw = request.query_params.get("customer.id")
    if raw is None:
        raise bad_request("customer.id is required")
    customer_id = parse_id("customer", raw)
    with tx() as conn:
        _customer(conn, customer_id)
        return TmfJSONResponse(consent_body(conn, customer_id))


@router.post("/consent")
def post_consent(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    data = parse(ConsentIn, body)
    if data.channel not in repo.CHANNELS:
        raise unprocessable(f"channel must be one of {', '.join(repo.CHANNELS)}")
    with tx() as conn:
        _customer(conn, data.customer.id)
        repo.record_consent(
            conn,
            customer_id=data.customer.id,
            channel=data.channel,
            opted_in=data.opted_in,
            source=data.source,
            operator=operator_of(request) or "unknown",
            now=clock.now(),
        )
        response = consent_body(conn, data.customer.id)
    return TmfJSONResponse(response, status_code=201)


@router.post("/message/{message_id}/resend")
def resend_message(message_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = repo.get(conn, parse_id("communicationMessage", message_id))
        if row is None:
            raise not_found("communicationMessage", message_id)
        if row["state"] in repo.OPEN:
            raise conflict("the message has not been sent yet: wait for it or cancel it")
        if row["receiver_email"] is None and row["receiver_phone"] is None:
            raise conflict("the message had no receiver address: nothing to send again")
        now = clock.now()
        copy = repo.insert(
            conn,
            event_type=row["event_type"],
            category=row["category"],
            channel=row["channel"],
            template=row["template"],
            state="initial",
            state_reason=None,
            receiver_name=row["receiver_name"],
            receiver_email=row["receiver_email"],
            receiver_phone=row["receiver_phone"],
            customer_id=row["customer_id"],
            billing_account_id=row["billing_account_id"],
            product_id=row["product_id"],
            subject=row["subject"],
            content=row["content"],
            now=now,
            scheduled_send_time=(
                service.quiet_until(now, billing_tz()) if row["channel"] == "sms" else None
            ),
            description=row["description"],
            priority=row["priority"],
            sender=row["sender"],
            resend_of=row["id"],
            customer_bill_id=row.get("customer_bill_id"),
        )
        assert copy is not None
        body = message_body(conn, copy)
    response = created(body)
    response.headers["Location"] = message_href(copy["id"])
    return response
