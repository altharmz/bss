"""TMF678 requests queue work; only the billing worker creates the bill."""

from datetime import timedelta

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation

from app.api import listing
from app.api.account import account_ref
from app.api.bill import BASE, bill_href
from app.api.common import (
    BillingAccountRef,
    TmfModel,
    check_type,
    created,
    parse,
    parse_id,
    ref,
    to_billing_date,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.repository import parties
from app.repository._sql import fetch_one, returning, select_page

router = APIRouter(prefix=BASE)
ATTRIBUTES = ("billingAccount", "customerBill", "state", "name", "description", "lastUpdate")


class DemandCreate(TmfModel):
    billing_account: BillingAccountRef
    name: str | None = None
    description: str | None = None


def demand_body(conn, row):
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/customerBillOnDemand/{row['id']}",
            "@type": "CustomerBillOnDemand",
            "state": row["state"],
            "name": row["name"],
            "description": row["description"],
            "lastUpdate": utc(row["updated_at"]),
            "billingAccount": account_ref(
                parties.get_billing_account(conn, row["billing_account_id"])
            ),
            "customerBill": ref(
                "CustomerBill", bill_href(row["customer_bill_id"]), row["customer_bill_id"]
            )
            if row["customer_bill_id"]
            else None,
        }
    )


@router.get("/customerBillOnDemand")
def list_bill_demands(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
            "state": ("state", listing.as_str),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = select_page(
            conn,
            "customer_bill_on_demand",
            where=query.where,
            offset=query.offset,
            limit=query.limit,
        )
        items = [demand_body(conn, r) for r in rows]
    return listing.list_response(items, total, query)


@router.get("/customerBillOnDemand/{demand_id}")
def get_bill_demand(demand_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = fetch_one(
            conn,
            "SELECT * FROM customer_bill_on_demand WHERE id = %s",
            (parse_id("customerBillOnDemand", demand_id),),
        )
        if row is None:
            raise not_found("customerBillOnDemand", demand_id)
        return TmfJSONResponse(listing.select_fields(demand_body(conn, row), fields))


@router.post("/customerBillOnDemand")
def create_bill_demand(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "CustomerBillOnDemand")
    data = parse(DemandCreate, body, read_only=("state", "customerBill", "lastUpdate"))
    with tx() as conn:
        if parties.get_billing_account(conn, data.billing_account.id) is None:
            raise unprocessable("billingAccount does not exist")
        now = clock.now()
        try:
            with conn.transaction():
                row = returning(
                    conn,
                    """
                    INSERT INTO customer_bill_on_demand
                        (billing_account_id, requested_at, ready_on, updated_at, name, description)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING *
                """,
                    (
                        data.billing_account.id,
                        now,
                        to_billing_date(now) + timedelta(days=1),
                        now,
                        data.name,
                        data.description,
                    ),
                )
        except UniqueViolation:
            raise conflict("an on-demand request is already in progress for this account") from None
        return created(listing.select_fields(demand_body(conn, row), fields))
