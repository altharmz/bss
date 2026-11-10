"""Prepaid command journal for operators (P3i), under /internal/v1/prepaid/command.
Ours, not TMF: internal states are not TMF enums.

Read: commands newest acceptance first (filter billingAccount.id, state), one command,
with its outcome, technical error, current needs-top-up reason, attempt times and the
order, products, run and bills linked to it. Write: **the API never posts.** A retry
only returns a failedTechnical command to pending with the same identity, request,
hold and attempt count; the billing worker attempts it on its next tick.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.bill import bill_href
from app.api.common import parse_id, utc
from app.api.deps import ClockDep, Tx
from app.api.errors import bad_request, conflict, not_found
from app.api.inventory import order_href, product_href
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query
from app.repository import prepaid_command_views as views
from app.repository import prepaid_commands as commands
from app.repository._sql import Row

BASE = "/internal/v1/prepaid/command"
router = APIRouter(prefix=BASE)

STATES = ("pending", "completed", "failedTechnical")


def command_body(row: Row, link: dict[str, Any]) -> dict[str, Any]:
    result = row["result"] or {}
    shortfall = link["shortfall"]
    run = link["run"]
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "state": row["state"],
        "billingAccount": {"id": str(row["billing_account_id"]), "name": row["account_name"]},
        "dueDate": row["due_date"],
        "createdAt": utc(row["created_at"]),
        "attempts": row["attempts"],
        "attemptedAt": None if row["attempted_at"] is None else utc(row["attempted_at"]),
        "nextRetryAt": None if row["next_retry_at"] is None else utc(row["next_retry_at"]),
        "outcome": result.get("outcome"),
        "reason": result.get("reason"),
        "error": row["error"],
        "needs_topup": None
        if shortfall is None
        else {
            "required": shortfall["required"],
            "available": shortfall["available"],
            "shortfall": shortfall["required"] - shortfall["available"],
            "observedAt": utc(shortfall["observed_at"]),
        },
        "productOrder": None
        if link["order"] is None
        else {"id": link["order"], "href": order_href(link["order"])},
        "products": [{"id": p, "href": product_href(p)} for p in link["products"]],
        "billingRun": None if run is None else {"id": str(run["id"]), "runDate": run["run_date"]},
        "bills": [{"id": str(b), "href": bill_href(b)} for b in link["bills"]],
    }


def _one(conn, raw_id: str) -> Row:
    row = views.get(conn, parse_id("prepaidCommand", raw_id))
    if row is None:
        raise not_found("prepaidCommand", raw_id)
    return row


def _single(request: Request, name: str) -> str | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    return given[0] if given else None


@router.get("")
def list_prepaid_commands(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("billingAccount.id", "state"))
    account = _single(request, "billingAccount.id")
    state = _single(request, "state")
    if state is not None and state not in STATES:
        raise bad_request(f"state must be one of {', '.join(STATES)}")
    try:
        account_id = None if account is None else UUID(account)
    except ValueError:
        raise bad_request("billingAccount.id must be a UUID") from None
    with tx() as conn:
        rows, more = views.page(
            conn, account_id=account_id, state=state, offset=offset, limit=limit
        )
        linked = views.links(conn, rows)
    items = [command_body(row, linked[row["id"]]) for row in rows]
    return page_response(request, items, more, offset, limit)


@router.get("/{command_id}")
def get_prepaid_command(command_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = _one(conn, command_id)
        link = views.links(conn, [row])[row["id"]]
    return TmfJSONResponse(command_body(row, link))


@router.post("/{command_id}/retry")
def retry_prepaid_command(
    command_id: str, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = _one(conn, command_id)
        if row["state"] != "failedTechnical":
            raise conflict(
                f"only a failedTechnical command can be retried; this one is {row['state']}"
            )
        try:
            commands.retry(conn, row["id"], now=clock.now())
        except ValueError as refused:  # changed concurrently, or a clock before its history
            raise conflict(str(refused)) from None
        row = views.get(conn, row["id"])
        link = views.links(conn, [row])[row["id"]]
    return TmfJSONResponse(command_body(row, link), status_code=202)
