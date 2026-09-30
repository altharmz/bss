"""TMF629 Customer Management v5: customer.

A customer is the role a party plays towards us; `engagedParty` is fixed at creation.
"""

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field

from app.api import listing
from app.api.common import (
    Ref,
    TmfModel,
    check_type,
    columns,
    created,
    not_null,
    parse,
    parse_id,
    ref,
)
from app.api.deps import JsonBody, MergePatchBody, Tx
from app.api.errors import not_found, unprocessable
from app.api.party import party_href, party_name
from app.api.tmfjson import TmfJSONResponse
from app.repository import parties
from app.repository._sql import Row

BASE = "/tmf-api/customerManagement/v5"
router = APIRouter(prefix=BASE)

CustomerStatus = Literal["Created", "Active", "Inactive"]
ATTRIBUTES = ("name", "status", "engagedParty")


class CustomerCreate(TmfModel):
    name: str = Field(min_length=1)
    engaged_party: Ref
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
def create_customer(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(CustomerCreate, body)
    with tx() as conn:
        if parties.get_party(conn, data.engaged_party.id) is None:
            raise unprocessable(f"engagedParty {data.engaged_party.id} does not exist")
        row = parties.create_customer(
            conn, party_id=data.engaged_party.id, name=data.name, status=data.status
        )
        new = customer_body(conn, row)
    return created(new)


@router.patch("/customer/{customer_id}")
def patch_customer(customer_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "Customer")
    data = parse(CustomerPatch, body, read_only=("engagedParty",))
    not_null(data, "name", "status")
    with tx() as conn:
        uid = parse_id("customer", customer_id)
        if parties.get_customer(conn, uid) is None:
            raise not_found("customer", customer_id)
        row = parties.update_customer(conn, uid, columns(data))
        patched = customer_body(conn, row)
    return TmfJSONResponse(patched)
