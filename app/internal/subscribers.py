"""GET /internal/v1/subscriber: the Subscribers screen's list (#314).

Billing accounts with their customer, oldest first, paged on the server with Link
rel=next and no count (#312), and searched by prefix (decided by the human 2026-11-10):
a customer or account name, a subscriber number on a line today, or a short id. Each row
carries what the table shows (balance, active products, how many accounts its customer
has), computed for the page only.
"""

from typing import Any

from fastapi import APIRouter, Request

from app.api.common import CENT, money
from app.api.deps import Tx
from app.api.errors import bad_request
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.repository import accounts, billing
from app.repository import subscribers as repo
from app.repository._sql import Row, rows_by_id

BASE = "/internal/v1/subscriber"
router = APIRouter(prefix=BASE)

STATES = ("Active", "Suspended", "Closed")
RATING_TYPES = ("prepaid", "postpaid")


def one_param(request: Request, name: str, allowed: tuple[str, ...] | None = None) -> str | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    if not given:
        return None
    if allowed is not None and given[0] not in allowed:
        raise bad_request(f"{name} must be one of {', '.join(allowed)}")
    if len(given[0]) > 200:
        raise bad_request(f"{name} is too long")
    return given[0]


def subscriber_body(
    row: Row, balance: Any, financial: Row | None, active: int, accounts_of_customer: int
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "state": row["state"],
        "ratingType": row["rating_type"],
        "customer": {"id": str(row["customer_id"]), "name": row["customer_name"]},
        "currency": row["currency"],
        "balance": money(balance.quantize(CENT), row["currency"]),
        "creditLimit": (
            None if row["credit_limit"] is None else money(row["credit_limit"], row["currency"])
        ),
        "cycleDay": row["bill_cycle_day"],
        "financialAccount": (
            None if financial is None else {"id": str(financial["id"]), "name": financial["name"]}
        ),
        "activeProducts": active,
        "customerAccounts": accounts_of_customer,
    }


@router.get("")
def search_subscribers(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("q", "state", "ratingType"))
    q = one_param(request, "q")
    state = one_param(request, "state", STATES)
    rating_type = one_param(request, "ratingType", RATING_TYPES)
    with tx() as conn:
        rows, more = repo.search(
            conn, q=q, state=state, rating_type=rating_type, offset=offset, limit=limit
        )
        ids = [row["id"] for row in rows]
        balances = billing.account_balances(conn, ids)
        financials = rows_by_id(
            conn,
            accounts.FINANCIAL,
            [r["financial_account_id"] for r in rows if r["financial_account_id"]],
        )
        active = repo.active_products(conn, ids)
        per_customer = repo.customer_accounts(conn, [row["customer_id"] for row in rows])
    items = [
        subscriber_body(
            row,
            balances[row["id"]],
            financials.get(row["financial_account_id"]),
            active.get(row["id"], 0),
            per_customer.get(row["customer_id"], 1),
        )
        for row in rows
    ]
    return page_response(request, items, more, offset, limit)
