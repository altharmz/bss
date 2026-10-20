"""TMF639 subscriber numbers. Assignment state is derived; only orders assign numbers."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator

from app.api import listing, specschema
from app.api.common import (
    check_server_set,
    check_type,
    claim_id,
    created,
    documents_model,
    merge_documents,
    not_null,
    parse_documented,
    parse_id,
    take_id,
    take_server_set,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.events import emit
from app.repository import numbers as repo
from app.repository._sql import delete_by_id, jsonb

API = "TMF639"
BASE = "/tmf-api/resourceInventory/v5"
router = APIRouter(prefix=BASE)
SPECIFICATION = {"id": "msisdn", "@type": "ResourceSpecificationRef"}
# Descriptive documents carry no provisioning or assignment behaviour.
DOCUMENTS = (
    "category",
    "validFor",
    "note",
    "place",
    "relatedParty",
    "attachment",
    "externalIdentifier",
    "resourceCharacteristic",
    "resourceVersion",
    "resourceOrderItem",
    "supportingResource",
    "resourceRelationship",
    "intent",
    "activationFeature",
    "startOperatingDate",
    "endOperatingDate",
)
SERVER_SET = ("creationDate", "usageState", "allocationStatus", "resourceSpecification")
ResourceDocuments = documents_model("ResourceDocuments", API, (*DOCUMENTS, *SERVER_SET))
ATTRIBUTES = ("name", "description", "value", "administrativeState", *SERVER_SET, *DOCUMENTS)


class ResourceCreate(ResourceDocuments):
    name: str = Field(min_length=1)
    value: str
    description: str | None = None
    administrative_state: Literal["locked", "unlocked"] = "unlocked"

    @field_validator("value")
    @classmethod
    def canonical_number(cls, value):
        if not (
            8 <= len(value) <= 15 and value.isascii() and value.isdecimal() and value[0] != "0"
        ):
            raise ValueError("must be E.164 digits without formatting (8..15, first digit 1..9)")
        return value


class ResourcePatch(ResourceDocuments):
    name: str | None = Field(default=None, min_length=1)
    value: str | None = None
    description: str | None = None
    administrative_state: Literal["locked", "unlocked"] | None = None


def resource_body(conn, row, today):
    state = (
        {"usageState": row["usage_state"], "allocationStatus": row["allocation_status"]}
        if "usage_state" in row
        else repo.states(conn, row["id"], today, settings.msisdn_quarantine_days)
    )
    return (
        without_nulls(
            {
                "id": str(row["id"]),
                "href": f"{BASE}/resource/{row['id']}",
                "@type": "LogicalResource",
                "name": row["name"],
                "description": row["description"],
                "value": row["value"],
                "administrativeState": row["administrative_state"],
                "creationDate": utc(row["created_at"]),
                "resourceSpecification": SPECIFICATION,
            }
        )
        | row["attrs"]
        | row["tmf_meta"]
        | state
    )


def require(conn, uid, *, lock=False):
    row = repo.get(conn, uid, lock=lock)
    if row is None:
        raise not_found("resource", str(uid))
    return row


def metadata(data, previous=None):
    values = {}
    for name, key in (("at_base_type", "@baseType"), ("at_schema_location", "@schemaLocation")):
        if name in data.model_fields_set:
            values[key] = getattr(data, name)
    return jsonb(merge_documents(previous or {}, values))


def server_values(body):
    values = take_server_set(body, SERVER_SET)
    # Check nested structure even though these fields are compared rather than stored.
    normalized, problems = specschema.check(API, "LogicalResource_FVO", values)
    return normalized, problems


@router.get("/resource")
def list_resources(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("id", listing.as_uuid),
            "name": ("name", listing.as_str),
            "value": ("value", listing.as_str),
            "administrativeState": ("administrative_state", listing.as_str),
            "usageState": ("usage_state", listing.as_str),
            "allocationStatus": ("allocation_status", listing.as_str),
            "resourceSpecification.id": ("resource_specification_id", listing.as_str),
        },
        attributes=ATTRIBUTES,
    )
    today = clock.today()
    with tx() as conn:
        rows, total = repo.page(
            conn, query.where, query.offset, query.limit, today, settings.msisdn_quarantine_days
        )
        bodies = [resource_body(conn, row, today) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get("/resource/{resource_id}")
def get_resource(resource_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = require(conn, parse_id("resource", resource_id))
        return TmfJSONResponse(
            listing.select_fields(resource_body(conn, row, clock.today()), fields)
        )


@router.post("/resource")
def create_resource(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "LogicalResource")
    uid = take_id(body)
    server, problems = server_values(body)
    data, documents = parse_documented(
        ResourceCreate, body, DOCUMENTS, API, "LogicalResource_FVO", found=problems
    )
    with tx() as conn:
        claim_id(conn, "subscriber_number", uid)
        # The unique constraint also protects callers outside this API. This lock gives
        # concurrent API duplicates a useful 409 rather than an aborted transaction.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"subscriber_number:value:{data.value}",),
        )
        if conn.execute(
            "SELECT 1 FROM subscriber_number WHERE value = %s", (data.value,)
        ).fetchone():
            raise conflict("a resource with this number value already exists")
        now = clock.now()
        values = {
            "name": data.name,
            "value": data.value,
            "description": data.description,
            "administrative_state": data.administrative_state,
            "created_at": now,
            "updated_at": now,
            "attrs": jsonb(merge_documents({}, documents)),
            "tmf_meta": metadata(data),
        }
        if uid is not None:
            values["id"] = uid
        row = repo.create(conn, values)
        new = resource_body(conn, row, clock.today())
        check_server_set(server, new)
        emit.created(conn, API, "resource", new, at=now)
    return created(listing.select_fields(new, fields))


@router.patch("/resource/{resource_id}")
def patch_resource(
    resource_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "LogicalResource")
    server, problems = server_values(body)
    # TMF639's PATCH uses Resource, including LogicalResource properties.
    data, documents = parse_documented(
        ResourcePatch, body, DOCUMENTS, API, "LogicalResource", found=problems
    )
    with tx() as conn:
        uid = parse_id("resource", resource_id)
        row = require(conn, uid, lock=True)
        today, now = clock.today(), clock.now()
        before = resource_body(conn, row, today)
        check_server_set(server, before)
        not_null(data, "name", "value", "administrative_state")
        if "value" in data.model_fields_set and data.value != row["value"]:
            raise conflict("number value is immutable")
        old = emit.before(conn, API, "resource", lambda: before)
        changes = {
            key: getattr(data, key)
            for key in ("name", "description", "administrative_state")
            if key in data.model_fields_set
        }
        changes |= {
            "attrs": jsonb(merge_documents(row["attrs"], documents)),
            "tmf_meta": metadata(data, row["tmf_meta"]),
            "updated_at": now,
        }
        new = resource_body(conn, repo.update(conn, uid, changes), today)
        emit.changed(conn, API, "resource", old, new, at=now, state="administrativeState")
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete("/resource/{resource_id}", status_code=204, response_class=Response)
def delete_resource(resource_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("resource", resource_id)
        row = require(conn, uid, lock=True)
        if conn.execute(
            "SELECT 1 FROM number_assignment WHERE subscriber_number_id = %s LIMIT 1", (uid,)
        ).fetchone():
            raise conflict("a number with assignment history cannot be deleted")
        emit.deleted(
            conn, API, "resource", lambda: resource_body(conn, row, clock.today()), at=clock.now()
        )
        delete_by_id(conn, "subscriber_number", uid)
    return Response(status_code=204)
