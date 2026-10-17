"""TMF629 Customer Management v5: customer.

A customer is the role a party plays towards us; `engagedParty` is fixed at creation.
"""

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.common import (
    Ref,
    TmfModel,
    check_referred_type,
    check_type,
    columns,
    created,
    not_null,
    parse,
    parse_id,
    ref,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import not_found, unprocessable
from app.api.party import party_href, party_name
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import parties
from app.repository._sql import Row, count_references

API = "TMF629"
BASE = "/tmf-api/customerManagement/v5"
router = APIRouter(prefix=BASE)

CustomerStatus = Literal["Created", "Active", "Inactive"]
ATTRIBUTES = ("name", "status", "engagedParty", "account")


class CustomerCreate(TmfModel):
    name: str = Field(min_length=1)
    engaged_party: Ref  # Individual or Organization: checked against the party in POST
    status: CustomerStatus = "Created"


class CustomerPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    status: CustomerStatus | None = None


def customer_href(customer_id: Any) -> str:
    return f"{BASE}/customer/{customer_id}"


def customer_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    party = parties.get_party(conn, row["party_id"])
    assert party is not None  # foreign key
    return {
        "id": str(row["id"]),
        "href": customer_href(row["id"]),
        "name": row["name"],
        "status": row["status"],
        "engagedParty": ref(
            party["party_type"],
            party_href(party),
            party["id"],
            party_name(party),
            ref_type="PartyRef",
        ),
        "account": [
            ref(
                "BillingAccount",
                f"/tmf-api/accountManagement/v5/billingAccount/{a['id']}",
                a["id"],
                a["name"],
                ref_type="AccountRef",
            )
            for a in parties.accounts_of_customer(conn, row["id"])
        ],
        "@type": "Customer",
    }


@router.get("/customer")
def list_customers(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "status": ("status", listing.as_str),
            "engagedParty.id": ("party_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = parties.list_customers(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [customer_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/customer/{customer_id}")
def get_customer(customer_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = parties.get_customer(conn, parse_id("customer", customer_id))
        if row is None:
            raise not_found("customer", customer_id)
        body = customer_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post("/customer")
def create_customer(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Customer")
    data = parse(CustomerCreate, body)
    with tx() as conn:
        # FOR KEY SHARE until commit: a concurrent delete of the party waits for this
        party = parties.get_party(conn, data.engaged_party.id, "key share")
        if party is None:
            raise unprocessable(f"engagedParty {data.engaged_party.id} does not exist")
        check_referred_type(data.engaged_party, party["party_type"], "engagedParty")
        row = parties.create_customer(
            conn, party_id=data.engaged_party.id, name=data.name, status=data.status
        )
        new = customer_body(conn, row)
        emit.created(conn, API, "customer", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/customer/{customer_id}")
def patch_customer(
    customer_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Customer")
    data = parse(CustomerPatch, body, read_only=("engagedParty",))
    not_null(data, "name", "status")
    with tx() as conn:
        uid = parse_id("customer", customer_id)
        current = parties.get_customer(conn, uid)
        if current is None:
            raise not_found("customer", customer_id)
        old = emit.before(conn, API, "customer", lambda: customer_body(conn, current))
        row = parties.update_customer(conn, uid, columns(data))
        patched = customer_body(conn, row)
        emit.changed(conn, API, "customer", old, patched, at=clock.now(), state="status")
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/customer/{customer_id}", status_code=204, response_class=Response)
def delete_customer(customer_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a customer without billing accounts (they take it FOR KEY SHARE on create)."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("customer", customer_id)
        row = parties.get_customer(conn, uid, "update")
        if row is None:
            raise not_found("customer", customer_id)
        counts = count_references(conn, uid, {"billingAccount": ("billing_account", "customer_id")})
        refuse_if_referenced("customer", uid, counts, "delete them first")
        emit.deleted(conn, API, "customer", lambda: customer_body(conn, row), at=clock.now())
        delete_row(conn, "customer", uid)
    return Response(status_code=204)
