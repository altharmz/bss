"""Account security for operators, under /internal/v1/security. Ours, not TMF: TMF has no PIN,
lock or audit API (docs/decisions/accounts.md).

`GET/PUT /pin/{customerId}` the customer's account PIN status and its set or reset (an agent,
with a recorded reason; the PIN is hashed and **never returned**). `GET/PUT /lock/{productId}`
the SIM swap and port-out locks of a line (clearing one needs the customer's PIN, in the
`X-Account-Pin` header). `GET /audit` the read-only log of agent actions on accounts.
"""

from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from pydantic import StringConstraints

from app.api.account import account_href
from app.api.common import TmfModel, parse, parse_id, utc
from app.api.customer import customer_href
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import bad_request, not_found
from app.api.guards import actor_of
from app.api.inventory import product_href
from app.api.listing import parse_page
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query
from app.repository import security as repo
from app.repository._sql import Row, fetch_one
from app.security import guard

BASE = "/internal/v1/security"
router = APIRouter(prefix=BASE)

Pin = Annotated[str, StringConstraints(pattern=r"^[0-9]{6,8}$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class PinIn(TmfModel):
    pin: Pin
    reason: Reason


class LockIn(TmfModel):
    sim_swap: bool
    port_out: bool
    reason: Reason


def _ref(href, uid) -> dict[str, Any] | None:
    return None if uid is None else {"id": str(uid), "href": href(uid)}


def pin_body(customer_id: UUID, row: Row | None, now) -> dict[str, Any]:
    """The PIN's status. The hash and the PIN are never part of it."""
    locked = row is not None and row["locked_until"] is not None and row["locked_until"] > now
    return {
        "customer": _ref(customer_href, customer_id),
        "set": row is not None,
        "setAt": None if row is None else utc(row["set_at"]),
        "setBy": None if row is None else row["set_by"],
        "reason": None if row is None else row["reason"],
        "failedAttempts": 0 if row is None else row["failed_attempts"],
        "lockedUntil": utc(row["locked_until"]) if locked else None,
    }


def lock_body(product_id: UUID, row: Row | None) -> dict[str, Any]:
    return {
        "product": _ref(product_href, product_id),
        "simSwap": False if row is None else row["sim_swap"],
        "portOut": False if row is None else row["port_out"],
        "updatedAt": None if row is None else utc(row["updated_at"]),
        "updatedBy": None if row is None else row["updated_by"],
        "reason": None if row is None else row["reason"],
    }


def audit_body(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "at": utc(row["at"]),
        "operator": row["operator"],
        "role": row["role"],
        "customer": _ref(customer_href, row["customer_id"]),
        "billingAccount": _ref(account_href, row["billing_account_id"]),
        "product": _ref(product_href, row["product_id"]),
        "action": row["action"],
        "outcome": row["outcome"],
        "reason": row["reason"],
        "verification": row["verification"],
        "detail": row["detail"],
    }


def _customer(conn: psycopg.Connection, raw: str) -> UUID:
    uid = parse_id("customer", raw)
    if fetch_one(conn, "SELECT 1 FROM customer WHERE id = %s", (uid,)) is None:
        raise not_found("customer", raw)
    return uid


def _product(conn: psycopg.Connection, raw: str) -> UUID:
    uid = parse_id("product", raw)
    if fetch_one(conn, "SELECT 1 FROM product WHERE id = %s", (uid,)) is None:
        raise not_found("product", raw)
    return uid


@router.get("/pin/{customer_id}")
def get_pin(customer_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        uid = _customer(conn, customer_id)
        return TmfJSONResponse(pin_body(uid, repo.pin_of(conn, uid), clock.now()))


@router.put("/pin/{customer_id}")
def put_pin(
    customer_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    data = parse(PinIn, body)
    now = clock.now()
    actor = actor_of(request)
    with tx() as conn:
        uid = _customer(conn, customer_id)
        existing = repo.pin_of(conn, uid, lock=True)
        row = repo.store_pin(conn, uid, guard.hash_pin(data.pin), actor.operator, data.reason, now)
        guard.done(
            conn,
            actor,
            now,
            action="pin_reset" if existing else "pin_set",
            verification="none",
            customer_id=uid,
            account_id=repo.first_account(conn, uid),
            reason=data.reason,
        )
        return TmfJSONResponse(pin_body(uid, row, now))


@router.get("/lock/{product_id}")
def get_lock(product_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        uid = _product(conn, product_id)
        return TmfJSONResponse(lock_body(uid, repo.lock_of(conn, uid)))


@router.put("/lock/{product_id}")
def put_lock(
    product_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    data = parse(LockIn, body)
    now = clock.now()
    actor = actor_of(request)
    denial = None
    with tx() as conn:
        uid = _product(conn, product_id)
        current = repo.lock_of(conn, uid, update=True)
        owner = repo.customer_of_product(conn, uid)
        assert owner is not None
        loosens = current is not None and (
            (current["sim_swap"] and not data.sim_swap)
            or (current["port_out"] and not data.port_out)
        )
        tightens = data.sim_swap != (current is not None and current["sim_swap"]) or (
            data.port_out != (current is not None and current["port_out"])
        )
        mode = "none"
        detail = {"simSwap": data.sim_swap, "portOut": data.port_out}
        if loosens and owner["rating_type"] == "prepaid":
            try:
                # clearing a lock is what an attacker needs: it asks for the customer's PIN
                mode = guard.gate(
                    conn,
                    actor,
                    now,
                    action="lock_cleared",
                    customer_ids=[owner["customer_id"]],
                    product_id=uid,
                    account_id=owner["billing_account_id"],
                    detail=detail,
                )
            except guard.Refusal as refusal:
                denial = refusal
        if denial is None:
            row = repo.store_lock(
                conn,
                uid,
                sim_swap=data.sim_swap,
                port_out=data.port_out,
                operator=actor.operator,
                reason=data.reason,
                now=now,
            )
            if loosens or tightens:
                guard.done(
                    conn,
                    actor,
                    now,
                    action="lock_cleared" if loosens else "lock_set",
                    verification=mode,
                    customer_id=owner["customer_id"],
                    product_id=uid,
                    account_id=owner["billing_account_id"],
                    reason=data.reason,
                    detail=detail,
                )
            response = lock_body(uid, row)
    if denial is not None:
        raise denial.error()
    return TmfJSONResponse(response)


@router.get("/audit")
def list_audit(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(
        request,
        extra=("customer.id", "product.id", "billingAccount.id", "action", "outcome", "operator"),
    )
    where: dict[str, Any] = {}
    for name, column in (
        ("customer.id", "customer_id"),
        ("product.id", "product_id"),
        ("billingAccount.id", "billing_account_id"),
    ):
        given = request.query_params.getlist(name)
        if len(given) > 1:
            raise bad_request(f"{name} given more than once")
        if given:
            where[column] = parse_id(name.split(".")[0], given[0])
    for name, allowed in (("action", repo.ACTIONS), ("outcome", repo.OUTCOMES)):
        given = request.query_params.getlist(name)
        if len(given) > 1:
            raise bad_request(f"{name} given more than once")
        if given:
            if given[0] not in allowed:
                raise bad_request(f"{name} must be one of {', '.join(allowed)}")
            where[name] = given[0]
    if "operator" in request.query_params:
        where["operator"] = request.query_params["operator"]
    with tx() as conn:
        rows, total = repo.audit_page(conn, where, offset, limit)
    items = [audit_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )
