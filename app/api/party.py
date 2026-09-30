"""TMF632 Party Management v5: individual and organization.

Both live in the `party` table; `@type` comes from `party_type`. An individual is not
reachable under /organization and vice versa.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request
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
from app.api.deps import JsonBody, MergePatchBody, Tx
from app.api.errors import not_found
from app.api.tmfjson import TmfJSONResponse
from app.repository import parties
from app.repository._sql import Row

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
def create_individual(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(IndividualCreate, body)
    with tx() as conn:
        row = parties.create_individual(
            conn, given_name=data.given_name, family_name=data.family_name
        )
    return created(party_body(row))


@router.patch("/individual/{party_id}")
def patch_individual(party_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "Individual")
    data = parse(IndividualPatch, body)
    not_null(data, "given_name", "family_name")
    with tx() as conn:
        current = _get(conn, "Individual", "individual", party_id)
        row = parties.update_party(conn, current["id"], columns(data))
    return TmfJSONResponse(party_body(row))


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
def create_organization(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(OrganizationCreate, body)
    with tx() as conn:
        row = parties.create_organization(conn, name=data.name)
    return created(party_body(row))


@router.patch("/organization/{party_id}")
def patch_organization(party_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "Organization")
    data = parse(OrganizationPatch, body)
    not_null(data, "name")
    changes = {"organization_name": data.name} if "name" in data.model_fields_set else {}
    with tx() as conn:
        current = _get(conn, "Organization", "organization", party_id)
        row = parties.update_party(conn, current["id"], changes)
    return TmfJSONResponse(party_body(row))
