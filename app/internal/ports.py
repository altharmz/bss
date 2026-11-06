"""Number pool, port requests and Number Transfer PINs for operators, under /internal/v1.
Ours, not TMF: TMF has no porting, pool or PIN API (docs/decisions/numbers.md).

`POST /number/pool` asks the carrier for numbers of an area code; they become TMF639
inventory. `POST /port/in` records a port-in (the customer brings a number) that the billing
worker submits; **the API never contacts the carrier**. A rejected port-in is corrected and
resubmitted. `POST /port/pin` generates a Number Transfer PIN (shown once, time-limited)
that the BSS validates when the carrier reports a port-out.
"""

import re
import secrets
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import StringConstraints

from app.api.account import account_href
from app.api.common import TmfModel, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, bad_request, conflict, not_found, unprocessable
from app.api.guards import actor_of
from app.api.inventory import order_href, product_href
from app.api.listing import parse_page
from app.api.resource_inventory import API as RESOURCE_API
from app.api.resource_inventory import BASE as RESOURCE_BASE
from app.api.resource_inventory import resource_body
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.events import emit
from app.integrations.network import NetworkTemporary, NumberPoolExhausted, adapter
from app.internal.context import no_query
from app.repository import numbers as number_repo
from app.repository import ports as repo
from app.repository import products, security
from app.repository._sql import Row, fetch_one
from app.security import guard

PORT_BASE = "/internal/v1/port"
router = APIRouter(prefix="/internal/v1")

AREA_CODE = re.compile(r"^[2-9][0-9]{2}$")
MAX_POOL = 100
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Zip = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}(-[0-9]{4})?$")]
Pin = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class PortIn(TmfModel):
    number: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{7,14}$")]
    carrier_account_number: Text
    transfer_pin: Pin
    billing_zip: Zip
    first_name: Text
    last_name: Text


class PortInCorrection(TmfModel):
    transfer_pin: Pin
    carrier_account_number: Text | None = None
    billing_zip: Zip | None = None
    first_name: Text | None = None
    last_name: Text | None = None


class PinRequest(TmfModel):
    product_id: UUID


def area_code(value: str) -> str | None:
    return value[1:4] if len(value) == 11 and value.startswith("1") else None


def port_body(row: Row) -> dict[str, Any]:
    number = None
    if row["subscriber_number_id"] is not None:
        sid = str(row["subscriber_number_id"])
        number = {"id": sid, "href": f"{RESOURCE_BASE}/resource/{sid}"}

    def ref(key: str, href) -> dict[str, str] | None:
        return None if row[key] is None else {"id": str(row[key]), "href": href(row[key])}

    def at(key: str):
        return None if row[key] is None else utc(row[key])

    return {
        "id": str(row["id"]),
        "direction": row["direction"],
        "state": row["state"],
        "number": row["number_value"],
        "areaCode": area_code(row["number_value"]),
        "resource": number,
        "carrierAccountNumber": row["carrier_account_number"],
        "billingZip": row["billing_zip"],
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "carrierReference": row["carrier_reference"],
        "submissions": row["submissions"],
        "rejectionCode": row["rejection_code"],
        "rejectionReason": row["rejection_reason"],
        "attempts": row["attempts"],
        "nextAttemptAt": at("next_attempt_at"),
        "lastError": row["last_error"],
        "product": ref("product_id", product_href),
        "billingAccount": ref("billing_account_id", account_href),
        "productOrder": ref("product_order_id", order_href),
        "answeredAt": at("answered_at"),
        "createdAt": utc(row["created_at"]),
        "updatedAt": utc(row["updated_at"]),
        "completedAt": at("completed_at"),
    }


def _one(conn, raw_id: str, *, lock: bool = False) -> Row:
    row = repo.get(conn, parse_id("portRequest", raw_id), lock=lock)
    if row is None:
        raise not_found("portRequest", raw_id)
    return row


def _new_number(conn, value: str, origin: str, now, today) -> Row:
    row = number_repo.create(
        conn,
        {
            "name": f"Number {value}",
            "value": value,
            "resource_specification_id": "msisdn",
            "origin": origin,
            "area_code": area_code(value),
            "created_at": now,
            "updated_at": now,
        },
    )
    emit.created(conn, RESOURCE_API, "resource", resource_body(conn, row, today), at=now)
    return row


def _lock_value(conn, value: str) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"subscriber_number:value:msisdn:{value}",),
    )


# --- number pool ---


@router.post("/number/pool", status_code=201)
def request_pool(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    unknown = sorted(set(body) - {"areaCode", "count"})
    if unknown:
        raise bad_request(f"unknown attribute {unknown[0]}")
    code, count = body.get("areaCode"), body.get("count")
    if not isinstance(code, str) or not AREA_CODE.fullmatch(code):
        raise unprocessable("areaCode must be three digits, the first 2 to 9")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_POOL:
        raise unprocessable(f"count must be a whole number from 1 to {MAX_POOL}")
    try:
        values = adapter().request_numbers(code, count)
    except NumberPoolExhausted as error:
        raise conflict(str(error) or f"the carrier has no more numbers for {code}") from None
    except NetworkTemporary as error:
        raise TmfError(503, str(error) or "the carrier could not give numbers now") from None
    now, today = clock.now(), clock.today()
    created = []
    with tx() as conn:
        for value in sorted(set(values)):
            _lock_value(conn, value)
            if fetch_one(
                conn,
                "SELECT 1 FROM subscriber_number WHERE resource_specification_id = 'msisdn'"
                " AND value = %s",
                (value,),
            ):
                continue  # already ours
            created.append(_new_number(conn, value, "pool", now, today))
    return TmfJSONResponse(
        {
            "areaCode": code,
            "requested": count,
            "numbers": [
                {
                    "id": str(r["id"]),
                    "value": r["value"],
                    "href": f"{RESOURCE_BASE}/resource/{r['id']}",
                }
                for r in created
            ],
        },
        status_code=201,
    )


# --- number transfer PINs (declared before /port/{id}) ---


def _line(conn, product_id: UUID, today) -> tuple[Row, Row]:
    """(product, number) of a line with a current subscriber number."""
    product = products.lock_products(conn, [product_id]).get(product_id)
    if product is None:
        raise not_found("product", str(product_id))
    number = fetch_one(
        conn,
        "SELECT n.* FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE a.product_id = %s AND a.cancelled_at IS NULL"
        " AND a.resource_specification_id = 'msisdn' AND a.valid_for @> %s::date",
        (product_id, today),
    )
    if product["status"] not in ("active", "suspended") or number is None:
        raise conflict("the product is not an active line with a subscriber number")
    return product, number


@router.post("/port/pin", status_code=201)
def create_pin(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    data = parse(PinRequest, body)
    now = clock.now()
    actor = actor_of(request)
    denial = None
    with tx() as conn:
        product, number = _line(conn, data.product_id, clock.today())
        owner = security.customer_of_product(conn, product["id"])
        assert owner is not None
        try:
            mode = "none"  # postpaid keeps working as before
            if owner["rating_type"] == "prepaid":
                mode = guard.gate(
                    conn,
                    actor,
                    now,
                    action="number_transfer_pin",
                    customer_ids=[owner["customer_id"]],
                    product_id=product["id"],
                    account_id=owner["billing_account_id"],
                    lock="port_out",
                    detail={"number": number["value"]},
                )
        except guard.Refusal as refusal:
            denial = refusal  # raised after the commit: the audit entry and the count stay
        else:
            pin = f"{secrets.randbelow(10**6):06d}"
            row = repo.insert_pin(
                conn,
                pin_id=uuid4(),
                number_id=number["id"],
                product_id=product["id"],
                pin=pin,
                now=now,
                expires_at=now + timedelta(days=settings.number_transfer_pin_days),
            )
            guard.done(
                conn,
                actor,
                now,
                action="number_transfer_pin",
                verification=mode,
                customer_id=owner["customer_id"],
                product_id=product["id"],
                account_id=owner["billing_account_id"],
                detail={"number": number["value"], "numberTransferPin": str(row["id"])},
            )
    if denial is not None:
        raise denial.error()
    return TmfJSONResponse(pin_body(row, number["value"], now) | {"pin": pin}, status_code=201)


def pin_body(row: Row, value: str, now) -> dict[str, Any]:
    if row["used_at"] is not None:
        state = "used"
    elif row["revoked_at"] is not None:
        state = "revoked"
    elif row["expires_at"] <= now:
        state = "expired"
    else:
        state = "active"
    return {
        "id": str(row["id"]),
        "state": state,
        "number": value,
        "product": {"id": str(row["product_id"]), "href": product_href(row["product_id"])},
        "createdAt": utc(row["created_at"]),
        "expiresAt": utc(row["expires_at"]),
        "usedAt": None if row["used_at"] is None else utc(row["used_at"]),
        "revokedAt": None if row["revoked_at"] is None else utc(row["revoked_at"]),
    }


@router.get("/port/pin")
def list_pins(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    parse_page(request, extra=("product.id",))
    given = request.query_params.getlist("product.id")
    if len(given) != 1:
        raise bad_request("product.id is required, once")
    product_id = parse_id("product", given[0])
    with tx() as conn:
        rows = repo.pins_of(conn, product_id)
        items = []
        for row in rows:
            value = fetch_one(
                conn,
                "SELECT value FROM subscriber_number WHERE id = %s",
                (row["subscriber_number_id"],),
            )["value"]
            items.append(pin_body(row, value, clock.now()))
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(len(items)), "X-Result-Count": str(len(items))}
    )


# --- port requests ---


@router.post("/port/in", status_code=201)
def create_port_in(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    data = parse(PortIn, body)
    now, today = clock.now(), clock.today()
    with tx() as conn:
        _lock_value(conn, data.number)
        number = fetch_one(
            conn,
            "SELECT * FROM subscriber_number WHERE resource_specification_id = 'msisdn'"
            " AND value = %s FOR UPDATE",
            (data.number,),
        )
        if number is None:
            number = _new_number(conn, data.number, "port_in", now, today)
        else:
            if number["origin"] != "port_in":
                raise conflict("this number is already in the inventory")
            if repo.open_for_number(conn, number["id"]) is not None:
                raise conflict("this number already has a port-in request")
        row = repo.insert_in(
            conn,
            value=data.number,
            number_id=number["id"],
            carrier_account_number=data.carrier_account_number,
            billing_zip=data.billing_zip,
            first_name=data.first_name,
            last_name=data.last_name,
            transfer_pin=data.transfer_pin,
            now=now,
        )
    return TmfJSONResponse(port_body(row), status_code=201)


@router.get("/port")
def list_ports(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("direction", "state", "number"))
    direction = request.query_params.get("direction")
    state = request.query_params.get("state")
    number = request.query_params.get("number")
    if direction is not None and direction not in repo.DIRECTIONS:
        raise bad_request(f"direction must be one of {', '.join(repo.DIRECTIONS)}")
    if state is not None and state not in repo.STATES:
        raise bad_request(f"state must be one of {', '.join(repo.STATES)}")
    with tx() as conn:
        rows, total = repo.page(
            conn, direction=direction, state=state, number=number, offset=offset, limit=limit
        )
    items = [port_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )


@router.get("/port/{port_id}")
def get_port(port_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(port_body(_one(conn, port_id)))


@router.post("/port/{port_id}/resubmit", status_code=202)
def resubmit_port(
    port_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    data = parse(PortInCorrection, body)
    with tx() as conn:
        row = _one(conn, port_id, lock=True)
        if row["direction"] != "in" or row["state"] != "rejected":
            raise conflict(
                f"only a rejected port-in can be resubmitted; this one is {row['direction']}"
                f" and {row['state']}"
            )
        row = repo.resubmit(
            conn,
            row["id"],
            {
                "account": data.carrier_account_number or row["carrier_account_number"],
                "zip": data.billing_zip or row["billing_zip"],
                "first": data.first_name or row["first_name"],
                "last": data.last_name or row["last_name"],
                "pin": data.transfer_pin,
            },
            clock.now(),
        )
    return TmfJSONResponse(port_body(row), status_code=202)


@router.post("/port/{port_id}/cancel")
def cancel_port(port_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = _one(conn, port_id, lock=True)
        if row["direction"] != "in" or row["state"] not in repo.OPEN:
            raise conflict(
                f"only an open port-in can be cancelled; this one is {row['direction']}"
                f" and {row['state']}"
            )
        if repo.claimed(conn, row["subscriber_number_id"]):
            raise conflict("an order holds this number: cancel the order, which cancels the port")
        row = repo.cancel(conn, row["id"], clock.now())
    return TmfJSONResponse(port_body(row))
