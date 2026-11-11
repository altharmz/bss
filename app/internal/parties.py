"""GET /internal/v1/party and /internal/v1/partyCustomer: the Parties screen's lists and
the account owner picker (#314).

Parties and customers oldest first, paged on the server with Link rel=next and no count
(#312), searched by the start of a name (case-insensitive) or a short id, like the
Subscribers list. Each row carries what the screen shows (a party's customers and their
billing accounts, whether it owns accounts; a customer's party and billing accounts),
computed for the page only.
"""

from fastapi import APIRouter, Request

from app.api.deps import Tx
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.internal.subscribers import one_param
from app.repository import party_search as repo
from app.repository._sql import Row

router = APIRouter(prefix="/internal/v1")

KINDS = ("Individual", "Organization")
PARTY_STATUSES = ("initialized", "validated", "deceased", "closed")
CUSTOMER_STATUSES = ("Created", "Active", "Inactive")


def party_name(row: Row) -> str:
    if row["party_type"] == "Organization":
        return row["organization_name"]
    return " ".join(part for part in (row["given_name"], row["family_name"]) if part)


def _link(row: Row) -> dict[str, str]:
    return {"id": str(row["id"]), "name": row["name"]}


@router.get("/party")
def search_parties(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("kind", "q", "status"))
    kind = one_param(request, "kind", KINDS)
    q = one_param(request, "q")
    status = one_param(request, "status", PARTY_STATUSES)
    with tx() as conn:
        rows, more = repo.search_parties(
            conn, kind=kind, q=q, status=status, offset=offset, limit=limit
        )
        ids = [row["id"] for row in rows]
        customers = repo.customers_of(conn, ids)
        accounts = repo.accounts_of(conn, [c["id"] for cs in customers.values() for c in cs])
        owners = repo.account_owners(conn, ids)
    items = [
        {
            "id": str(row["id"]),
            "kind": row["party_type"],
            "name": party_name(row),
            "status": row["status"],
            "customers": [_link(c) for c in customers.get(row["id"], [])],
            "billingAccounts": [
                {**_link(a), "customerId": str(c["id"])}
                for c in customers.get(row["id"], [])
                for a in accounts.get(c["id"], [])
            ],
            "ownsAccounts": row["id"] in owners,
        }
        for row in rows
    ]
    return page_response(request, items, more, offset, limit)


@router.get("/partyCustomer")
def search_customers(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("q", "status"))
    q = one_param(request, "q")
    status = one_param(request, "status", CUSTOMER_STATUSES)
    with tx() as conn:
        rows, more = repo.search_customers(conn, q=q, status=status, offset=offset, limit=limit)
        accounts = repo.accounts_of(conn, [row["id"] for row in rows])
    items = [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "status": row["status"],
            "party": {
                "id": str(row["party_id"]),
                "kind": row["party_kind"],
                "name": row["party_name"],
            },
            "billingAccounts": [_link(a) for a in accounts.get(row["id"], [])],
        }
        for row in rows
    ]
    return page_response(request, items, more, offset, limit)
