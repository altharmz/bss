"""Network provisioning commands for operators, under /internal/v1/network/command.
Ours, not TMF (docs/decisions/network.md).

Read: the commands of order items, newest first (filter productOrder.id, product.id,
state), and one command with its state, attempts, last error, refusal code, profile and
the request as sent. Write: **the API never sends.** A retry returns an `exhausted`
command (temporary errors used up the automatic attempts) to pending with the same
identity and request; the billing worker sends it on its next tick. A refusal is final: 409.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.common import parse_id, utc
from app.api.deps import Tx
from app.api.errors import bad_request, conflict, not_found
from app.api.inventory import order_href, product_href
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query
from app.repository import network_commands as repo
from app.repository._sql import Row

BASE = "/internal/v1/network/command"
router = APIRouter(prefix=BASE)

STATES = ("pending", "confirmed", "refused", "exhausted", "cancelled")


def command_body(row: Row) -> dict[str, Any]:
    order = str(row["product_order_id"])
    product = None if row["product_id"] is None else str(row["product_id"])
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "state": row["state"],
        "productOrder": {"id": order, "href": order_href(order)},
        "productOrderItem": {"id": str(row["product_order_item_id"])},
        "product": None if product is None else {"id": product, "href": product_href(product)},
        "dueDate": row["due_date"],
        "profile": row["profile"],
        "request": row["request"],
        "attempts": row["attempts"],
        "attemptedAt": None if row["attempted_at"] is None else utc(row["attempted_at"]),
        "nextAttemptAt": None if row["next_attempt_at"] is None else utc(row["next_attempt_at"]),
        "lastError": row["last_error"],
        "refusalCode": row["refusal_code"],
        "confirmedAt": None if row["confirmed_at"] is None else utc(row["confirmed_at"]),
        "createdAt": utc(row["created_at"]),
    }


def _one(conn, raw_id: str) -> Row:
    row = repo.get(conn, parse_id("networkCommand", raw_id))
    if row is None:
        raise not_found("networkCommand", raw_id)
    return row


def _uuid(request: Request, name: str) -> UUID | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    try:
        return UUID(given[0]) if given else None
    except ValueError:
        raise bad_request(f"{name} must be a UUID") from None


@router.get("")
def list_network_commands(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("productOrder.id", "product.id", "state"))
    order, product = _uuid(request, "productOrder.id"), _uuid(request, "product.id")
    state = request.query_params.get("state")
    if state is not None and state not in STATES:
        raise bad_request(f"state must be one of {', '.join(STATES)}")
    with tx() as conn:
        rows, more = repo.page(
            conn, order_id=order, product_id=product, state=state, offset=offset, limit=limit
        )
    items = [command_body(row) for row in rows]
    return page_response(request, items, more, offset, limit)


@router.get("/{command_id}")
def get_network_command(command_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(command_body(_one(conn, command_id)))


@router.post("/{command_id}/retry")
def retry_network_command(command_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = repo.get(conn, _one(conn, command_id)["id"], lock=True)
        if row["state"] != "exhausted":
            raise conflict(
                f"only an exhausted network command can be retried; this one is {row['state']}"
                + (" (a refusal is final: place a new order)" if row["state"] == "refused" else "")
            )
        row = repo.retry(conn, row["id"])
    return TmfJSONResponse(command_body(row), status_code=202)
