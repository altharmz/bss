"""TMF632 Party Management v5: individual and organization.

Both live in the `party` table; `@type` comes from `party_type`. An individual is not
reachable under /organization and vice versa.
"""

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api import listing
from app.api.common import (
    check_type,
    claim_id,
    created,
    documents_model,
    merge_documents,
    meta_body,
    not_null,
    parse_documented,
    parse_id,
    take_id,
    with_meta,
    without_nulls,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import not_found
from app.api.tmfjson import TmfJSONResponse
from app.clock import Clock
from app.events import emit
from app.repository import accounts, parties
from app.repository._sql import Row, count_references

API = "TMF632"
BASE = "/tmf-api/partyManagement/v5"
router = APIRouter(prefix=BASE)

# Every other attribute the spec defines is a document to us: stored as sent (checked
# against the spec), no behaviour (docs/decisions/tmf-api.md, "Party and customer
# attributes"). The same in both specs' schemas of a party.
PARTY_DOCUMENTS = (
    "contactMedium",
    "creditRating",
    "externalReference",
    "partyCharacteristic",
    "relatedParty",
    "taxExemptionCertificate",
)
INDIVIDUAL_DOCUMENTS = (
    *PARTY_DOCUMENTS,
    "aristocraticTitle",
    "birthDate",
    "countryOfBirth",
    "deathDate",
    "disability",
    "familyNamePrefix",
    "formattedName",
    "gender",
    "generation",
    "individualIdentification",
    "languageAbility",
    "legalName",
    "location",
    "maritalStatus",
    "middleName",
    "name",
    "nationality",
    "otherName",
    "placeOfBirth",
    "preferredGivenName",
    "skill",
    "title",
)
ORGANIZATION_DOCUMENTS = (
    *PARTY_DOCUMENTS,
    "existsDuring",
    "isHeadOffice",
    "isLegalEntity",
    "nameType",
    "organizationChildRelationship",
    "organizationIdentification",
    "organizationParentRelationship",
    "organizationType",
    "otherName",
    "tradingName",
)
INDIVIDUAL_ATTRIBUTES = ("givenName", "familyName", "status", *INDIVIDUAL_DOCUMENTS)
ORGANIZATION_ATTRIBUTES = ("name", "status", *ORGANIZATION_DOCUMENTS)

# TMF632 IndividualStateType / OrganizationStateType: a record only, nothing depends on it
IndividualStatus = Literal["initialized", "validated", "deceased"]
OrganizationStatus = Literal["initialized", "validated", "closed"]


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
    body |= {"status": row["status"], **row["details"], "@type": row["party_type"]}
    return without_nulls(body) | meta_body(row)


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


IndividualDocuments = documents_model("IndividualDocuments", API, INDIVIDUAL_DOCUMENTS)


class IndividualCreate(IndividualDocuments):
    given_name: str = Field(min_length=1)
    family_name: str = Field(min_length=1)
    status: IndividualStatus | None = None


class IndividualPatch(IndividualDocuments):
    given_name: str | None = Field(default=None, min_length=1)
    family_name: str | None = Field(default=None, min_length=1)
    status: IndividualStatus | None = None


@router.get("/individual")
def list_individuals(request: Request, tx: Tx) -> TmfJSONResponse:
    filters = {
        "givenName": ("given_name", listing.as_str),
        "familyName": ("family_name", listing.as_str),
        "status": ("status", listing.as_str),
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
    uid = take_id(body)
    data, documents = parse_documented(
        IndividualCreate, body, INDIVIDUAL_DOCUMENTS, API, "Individual_FVO"
    )
    with tx() as conn:
        claim_id(conn, "party", uid)
        row = parties.create_individual(
            conn,
            given_name=data.given_name,
            family_name=data.family_name,
            status=data.status,
            details=merge_documents({}, documents),
            id=uid,
        )
        new = party_body(with_meta(conn, "party", row, data))
        emit.created(conn, API, "individual", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/individual/{party_id}")
def patch_individual(
    party_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, INDIVIDUAL_ATTRIBUTES)
    check_type(body, "Individual")
    data, documents = parse_documented(
        IndividualPatch, body, INDIVIDUAL_DOCUMENTS, API, "Individual_MVO"
    )
    not_null(data, "given_name", "family_name")
    changes = _changes(data, ("given_name", "family_name", "status"))
    with tx() as conn:
        current = _get(conn, "Individual", "individual", party_id)
        new = _patch(conn, "individual", current, changes, documents, data, clock)
    return TmfJSONResponse(listing.select_fields(new, fields))


def _changes(data: BaseModel, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(data, name) for name in names if name in data.model_fields_set}


def _patch(
    conn: psycopg.Connection,
    resource: str,
    current: Row,
    changes: dict[str, Any],
    documents: dict[str, Any],
    data: BaseModel,
    clock: Clock,
) -> dict[str, Any]:
    old = emit.before(conn, API, resource, lambda: party_body(current))
    if documents:
        changes["details"] = merge_documents(current["details"], documents)
    row = parties.update_party(conn, current["id"], changes)
    new = party_body(with_meta(conn, "party", row, data))
    emit.changed(conn, API, resource, old, new, at=clock.now(), state="status")
    return new


@router.delete("/individual/{party_id}", status_code=204, response_class=Response)
def delete_individual(party_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(request, tx, clock, "Individual", "individual", party_id)


# --- organization ---


OrganizationDocuments = documents_model("OrganizationDocuments", API, ORGANIZATION_DOCUMENTS)


class OrganizationCreate(OrganizationDocuments):
    name: str = Field(min_length=1)
    status: OrganizationStatus | None = None


class OrganizationPatch(OrganizationDocuments):
    name: str | None = Field(default=None, min_length=1)
    status: OrganizationStatus | None = None


@router.get("/organization")
def list_organizations(request: Request, tx: Tx) -> TmfJSONResponse:
    filters = {
        "name": ("organization_name", listing.as_str),
        "status": ("status", listing.as_str),
    }
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
    uid = take_id(body)
    data, documents = parse_documented(
        OrganizationCreate, body, ORGANIZATION_DOCUMENTS, API, "Organization_FVO"
    )
    with tx() as conn:
        claim_id(conn, "party", uid)
        row = parties.create_organization(
            conn,
            name=data.name,
            status=data.status,
            details=merge_documents({}, documents),
            id=uid,
        )
        new = party_body(with_meta(conn, "party", row, data))
        emit.created(conn, API, "organization", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/organization/{party_id}")
def patch_organization(
    party_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ORGANIZATION_ATTRIBUTES)
    check_type(body, "Organization")
    data, documents = parse_documented(
        OrganizationPatch, body, ORGANIZATION_DOCUMENTS, API, "Organization_MVO"
    )
    not_null(data, "name")
    changes = _changes(data, ("status",))
    if "name" in data.model_fields_set:
        changes["organization_name"] = data.name
    with tx() as conn:
        current = _get(conn, "Organization", "organization", party_id)
        new = _patch(conn, "organization", current, changes, documents, data, clock)
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete("/organization/{party_id}", status_code=204, response_class=Response)
def delete_organization(party_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(request, tx, clock, "Organization", "organization", party_id)
