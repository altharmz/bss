"""TMF632 Party Management v5: individual and organization.

Both live in the `party` table; `@type` comes from `party_type`. An individual is not
reachable under /organization and vice versa.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.common import (
    TmfModel,
    check_type,
    columns,
    created,
    not_null,
    parse,
    parse_id,
    without_nulls,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import not_found
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import accounts, parties
from app.repository._sql import Row, count_references

API = "TMF632"
BASE = "/tmf-api/partyManagement/v5"
router = APIRouter(prefix=BASE)

INDIVIDUAL_ATTRIBUTES = ("givenName", "familyName")
ORGANIZATION_ATTRIBUTES = ("name",)


def party_href(row: Row) -> str:
    resource = "individual" if row["party_type"] == "Individual" else "organization"
    return f"{BASE}/{resource}/{row['id']}"


def party_name(row: Row) -> str:
    if row["party_type"] == "Organization":
        return row["organization_name"]
    return " ".join(part for part in (row["given_name"], row["family_name"]) if part)


def party_body(row: Row) -> dict[str, Any]:
    body: dict[str, Any] = {"id": str(row["id"]), "href": party_href(row)}
    if row["party_type"] == "Individual":
        body |= {"givenName": row["given_name"], "familyName": row["family_name"]}
    else:
        body |= {"name": row["organization_name"]}
    body["@type"] = row["party_type"]
    return without_nulls(body)


def _get(conn: psycopg.Connection, party_type: str, resource: str, raw_id: str) -> Row:
    row = parties.get_party(conn, parse_id(resource, raw_id))
    if row is None or row["party_type"] != party_type:
        raise not_found(resource, raw_id)
    return row


def _delete(
    request: Request, tx: Tx, clock: ClockDep, party_type: str, resource: str, raw_id: str
) -> Response:
    """Only a party nothing refers to: no customer engages it, it owns no account.
    Customers and accounts take the party FOR KEY SHARE when they refer to it."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(resource, raw_id)
        row = parties.get_party(conn, uid, "update")
        if row is None or row["party_type"] != party_type:
            raise not_found(resource, raw_id)
        counts = count_references(
            conn,
            uid,
            {
                "customer": ("customer", "party_id"),
                "financialAccount": ("financial_account", "party_id"),
            },
        )
        for kind, n in accounts.count_party_accounts(conn, "party_id", uid).items():
            counts[kind[0].lower() + kind[1:]] = n
        refuse_if_referenced(resource, uid, counts, "delete those first")
        emit.deleted(conn, API, resource, party_body(row), at=clock.now())
        delete_row(conn, "party", uid)
    return Response(status_code=204)


def _list(request: Request, tx: Tx, party_type: str, filters, attributes) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=filters, attributes=attributes)
    with tx() as conn:
        rows, total = parties.list_parties(
            conn,
            where={"party_type": party_type, **query.where},
            offset=query.offset,
            limit=query.limit,
        )
    return listing.list_response([party_body(row) for row in rows], total, query)


# --- individual ---


class IndividualCreate(TmfModel):
    given_name: str = Field(min_length=1)
    family_name: str = Field(min_length=1)


class IndividualPatch(TmfModel):
    given_name: str | None = Field(default=None, min_length=1)
    family_name: str | None = Field(default=None, min_length=1)


@router.get("/individual")
def list_individuals(request: Request, tx: Tx) -> TmfJSONResponse:
    filters = {
        "givenName": ("given_name", listing.as_str),
        "familyName": ("family_name", listing.as_str),
    }
    return _list(request, tx, "Individual", filters, INDIVIDUAL_ATTRIBUTES)


@router.get("/individual/{party_id}")
def get_individual(party_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, INDIVIDUAL_ATTRIBUTES)
    with tx() as conn:
        row = _get(conn, "Individual", "individual", party_id)
    return TmfJSONResponse(listing.select_fields(party_body(row), fields))


@router.post("/individual")
def create_individual(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, INDIVIDUAL_ATTRIBUTES)
    check_type(body, "Individual")
    data = parse(IndividualCreate, body)
    with tx() as conn:
        row = parties.create_individual(
            conn, given_name=data.given_name, family_name=data.family_name
        )
        new = party_body(row)
        emit.created(conn, API, "individual", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/individual/{party_id}")
def patch_individual(
    party_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, INDIVIDUAL_ATTRIBUTES)
    check_type(body, "Individual")
    data = parse(IndividualPatch, body)
    not_null(data, "given_name", "family_name")
    with tx() as conn:
        current = _get(conn, "Individual", "individual", party_id)
        old = emit.before(conn, API, "individual", lambda: party_body(current))
        row = parties.update_party(conn, current["id"], columns(data))
        new = party_body(row)
        emit.changed(conn, API, "individual", old, new, at=clock.now())
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete("/individual/{party_id}", status_code=204, response_class=Response)
def delete_individual(party_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(request, tx, clock, "Individual", "individual", party_id)


# --- organization ---


class OrganizationCreate(TmfModel):
    name: str = Field(min_length=1)


class OrganizationPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)


@router.get("/organization")
def list_organizations(request: Request, tx: Tx) -> TmfJSONResponse:
    filters = {"name": ("organization_name", listing.as_str)}
    return _list(request, tx, "Organization", filters, ORGANIZATION_ATTRIBUTES)


@router.get("/organization/{party_id}")
def get_organization(party_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ORGANIZATION_ATTRIBUTES)
    with tx() as conn:
        row = _get(conn, "Organization", "organization", party_id)
    return TmfJSONResponse(listing.select_fields(party_body(row), fields))


@router.post("/organization")
def create_organization(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ORGANIZATION_ATTRIBUTES)
    check_type(body, "Organization")
    data = parse(OrganizationCreate, body)
    with tx() as conn:
        row = parties.create_organization(conn, name=data.name)
        new = party_body(row)
        emit.created(conn, API, "organization", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/organization/{party_id}")
def patch_organization(
    party_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ORGANIZATION_ATTRIBUTES)
    check_type(body, "Organization")
    data = parse(OrganizationPatch, body)
    not_null(data, "name")
    changes = {"organization_name": data.name} if "name" in data.model_fields_set else {}
    with tx() as conn:
        current = _get(conn, "Organization", "organization", party_id)
        old = emit.before(conn, API, "organization", lambda: party_body(current))
        row = parties.update_party(conn, current["id"], changes)
        new = party_body(row)
        emit.changed(conn, API, "organization", old, new, at=clock.now())
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete("/organization/{party_id}", status_code=204, response_class=Response)
def delete_organization(party_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(request, tx, clock, "Organization", "organization", party_id)
