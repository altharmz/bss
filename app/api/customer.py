"""TMF629 Customer Management v5: customer.

A customer is the role a party plays towards us. `engagedParty` changes only while the
customer has no billing account: the accounts' financial accounts belong to that party.
"""

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.common import (
    Ref,
    check_referred_type,
    check_server_set,
    check_type,
    claim_id,
    created,
    documents_model,
    merge_documents,
    meta_body,
    not_null,
    parse_documented,
    parse_id,
    ref,
    take_id,
    take_server_set,
    with_meta,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.guards import authorize_contact
from app.api.party import party_href, party_name
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import parties
from app.repository._sql import Row, count_references

API = "TMF629"
BASE = "/tmf-api/customer/v5"
router = APIRouter(prefix=BASE)

CustomerStatus = Literal["Created", "Active", "Inactive"]
# Documents to us: stored as sent (checked against the spec), no behaviour - a customer's
# validFor does not stop orders or billing (docs/decisions/tmf-api.md, "Party and customer
# attributes").
DOCUMENTS = (
    "agreement",
    "characteristic",
    "contactMedium",
    "creditProfile",
    "description",
    "partyRoleSpecification",
    "paymentMethod",
    "relatedParty",
    "role",
    "statusReason",
    "validFor",
)
ATTRIBUTES = ("name", "status", "engagedParty", "account", *DOCUMENTS)

CustomerDocuments = documents_model("CustomerDocuments", API, DOCUMENTS)


class CustomerCreate(CustomerDocuments):
    name: str = Field(min_length=1)
    engaged_party: Ref  # Individual or Organization: checked against the party in POST
    status: CustomerStatus = "Created"


class CustomerPatch(CustomerDocuments):
    name: str | None = Field(default=None, min_length=1)
    status: CustomerStatus | None = None
    engaged_party: Ref | None = None


# `account` is the customer's billing accounts, linked from the account's side
# (relatedParty customer): accepted when it is the list we answer, as the identity
# Customer_FVO and Customer_MVO carry.
SERVER_SET = ("id", "href", "account")


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
        **row["details"],
        "@type": "Customer",
        **meta_body(row),
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
    uid = take_id(body)
    server_set = take_server_set(body, ("href", "account"))
    data, documents = parse_documented(CustomerCreate, body, DOCUMENTS, API, "Customer_FVO")
    with tx() as conn:
        _engaged_party(conn, data.engaged_party)
        claim_id(conn, "customer", uid)
        row = parties.create_customer(
            conn,
            party_id=data.engaged_party.id,
            name=data.name,
            status=data.status,
            details=merge_documents({}, documents),
            id=uid,
        )
        new = customer_body(conn, with_meta(conn, "customer", row, data))
        check_server_set(server_set, new)
        emit.created(conn, API, "customer", new, at=clock.now())
    return created(listing.select_fields(new, fields))


def _engaged_party(conn: psycopg.Connection, reference: Ref) -> None:
    # FOR KEY SHARE until commit: a concurrent delete of the party waits for this
    party = parties.get_party(conn, reference.id, "key share")
    if party is None:
        raise unprocessable(f"engagedParty {reference.id} does not exist")
    check_referred_type(reference, party["party_type"], "engagedParty")


@router.patch("/customer/{customer_id}")
def patch_customer(
    customer_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Customer")
    server_set = take_server_set(body, SERVER_SET)
    data, documents = parse_documented(CustomerPatch, body, DOCUMENTS, API, "Customer_MVO")
    not_null(data, "name", "status", "engaged_party")
    ticket = authorize_contact(request, tx, clock.now(), "customer", customer_id, documents)
    with tx() as conn:
        uid = parse_id("customer", customer_id)
        # FOR UPDATE: a billing account created meanwhile (it takes the customer FOR KEY
        # SHARE) waits, so the engagedParty check below cannot miss it
        current = parties.get_customer(conn, uid, "update")
        if current is None:
            raise not_found("customer", customer_id)
        before = customer_body(conn, current)
        check_server_set(server_set, before)
        changes = {n: getattr(data, n) for n in ("name", "status") if n in data.model_fields_set}
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        if data.engaged_party is not None and data.engaged_party.id != current["party_id"]:
            if parties.accounts_of_customer(conn, uid):
                raise conflict(
                    "engagedParty cannot change while the customer has billing accounts"
                    " (their financial accounts belong to the party)"
                )
            _engaged_party(conn, data.engaged_party)
            changes["party_id"] = data.engaged_party.id
        elif data.engaged_party is not None:
            party_type = before["engagedParty"]["@referredType"]
            check_referred_type(data.engaged_party, party_type, "engagedParty")
        old = emit.before(conn, API, "customer", lambda: before)
        row = parties.update_customer(conn, uid, changes)
        patched = customer_body(conn, with_meta(conn, "customer", row, data))
        emit.changed(conn, API, "customer", old, patched, at=clock.now(), state="status")
        if ticket is not None:
            ticket.record(conn, clock.now())
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
