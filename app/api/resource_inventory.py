"""TMF639 subscriber numbers. Assignment state is derived; only orders assign numbers."""

import re
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field

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
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.events import emit
from app.repository import numbers as repo
from app.repository import sims
from app.repository._sql import delete_by_id, jsonb

API = "TMF639"
BASE = "/tmf-api/resourceInventory/v5"
router = APIRouter(prefix=BASE)
KINDS = ("msisdn", "imsi")
ICCID = re.compile(r"^89[0-9]{16,18}$")


def specification(kind):
    return {"id": kind, "@type": "ResourceSpecificationRef"}


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
# PhysicalResource attributes of a physical SIM, derived from the stock columns
SIM_SET = ("serialNumber", "batchNumber")
# characteristics derived from the stock columns of a typed SIM (docs/decisions/sim.md)
SIM_DERIVED = ("simType", "simState", "smdpAddress", "matchingId", "activationCode")
ResourceDocuments = documents_model("ResourceDocuments", API, (*DOCUMENTS, *SERVER_SET))
ATTRIBUTES = (
    "name",
    "description",
    "value",
    "administrativeState",
    *SERVER_SET,
    *SIM_SET,
    *DOCUMENTS,
)


class ResourceCreate(ResourceDocuments):
    name: str = Field(min_length=1)
    value: str
    description: str | None = None
    administrative_state: Literal["locked", "unlocked"] = "unlocked"


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
                "@type": "PhysicalResource" if row["sim_type"] == "physical" else "LogicalResource",
                "name": row["name"],
                "description": row["description"],
                "value": row["value"],
                "administrativeState": row["administrative_state"],
                "creationDate": utc(row["created_at"]),
                "resourceSpecification": specification(row["resource_specification_id"]),
            }
        )
        | row["attrs"]
        | characteristics(row)
        | sim_attributes(row)
        | row["tmf_meta"]
        | state
    )


def sim_attributes(row):
    """A physical SIM is a PhysicalResource: its ICCID is the serial number."""
    if row["sim_type"] != "physical":
        return {}
    return without_nulls({"serialNumber": row["iccid"], "batchNumber": row["sim_batch"]})


def activation_code(row):
    """The LPA activation code of an eSIM profile; None for any other resource."""
    if row["sim_type"] != "esim":
        return None
    return f"LPA:1${row['esim_smdp_address']}${row['esim_matching_id']}"


def sim_characteristics(row):
    """simType and simState of a typed SIM; an eSIM profile's activation code only while
    it is reserved or active (a stocked profile is a secret nobody should install)."""
    if row["sim_type"] is None:
        return []
    values = {"simType": row["sim_type"], "simState": row["sim_state"]}
    if row["sim_type"] == "esim" and row["sim_state"] in ("reserved", "active"):
        values |= {
            "smdpAddress": row["esim_smdp_address"],
            "matchingId": row["esim_matching_id"],
            "activationCode": activation_code(row),
        }
    return [
        {"@type": "StringCharacteristic", "name": k, "valueType": "string", "value": v}
        for k, v in values.items()
    ]


def characteristics(row):
    """The stored descriptive characteristics, plus the ICCID kept in its own column."""
    stored = list(row["attrs"].get("resourceCharacteristic") or [])
    if row["iccid"] is not None:
        stored.append(
            {
                "@type": "StringCharacteristic",
                "name": "iccid",
                "valueType": "string",
                "value": row["iccid"],
            }
        )
    stored += sim_characteristics(row)
    return {"resourceCharacteristic": stored} if stored else {}


def split_iccid(kind, documents):
    """(iccid or None, documents without the iccid characteristic). 422 when invalid."""
    sent = documents.get("resourceCharacteristic")
    if not sent:
        return None, documents
    iccid = None
    rest = []
    for item in sent:
        if item.get("name") == "iccid":
            iccid = item.get("value")
        else:
            rest.append(item)
    if iccid is None:
        return None, documents
    if kind != "imsi":
        raise unprocessable("the iccid characteristic is allowed only on imsi resources")
    if not isinstance(iccid, str) or not ICCID.fullmatch(iccid):
        raise unprocessable("iccid must be 18..20 digits starting with 89")
    return iccid, {**documents, "resourceCharacteristic": rest or None}


def drop_derived(row, body, documents):
    """A typed SIM's derived characteristics may be echoed back unchanged, never edited."""
    sent = documents.get("resourceCharacteristic")
    if not sent or row["sim_type"] is None:
        return documents
    current = {c["name"]: c for c in body.get("resourceCharacteristic", [])}
    kept = []
    for item in sent:
        name = item.get("name")
        if name in SIM_DERIVED:
            if name not in current or item.get("value") != current[name].get("value"):
                raise unprocessable(f"the {name} characteristic is set by the server")
        else:
            kept.append(item)
    return {**documents, "resourceCharacteristic": kept or None}


def check_value(kind, value):
    if kind == "imsi":
        ok = 14 <= len(value) <= 15 and value.isascii() and value.isdecimal()
        message = "must be 14 or 15 IMSI digits without formatting"
    else:
        ok = 8 <= len(value) <= 15 and value.isascii() and value.isdecimal() and value[0] != "0"
        message = "must be E.164 digits without formatting (8..15, first digit 1..9)"
    if not ok:
        raise unprocessable(f"value {message}")


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
    values = take_server_set(body, (*SERVER_SET, *SIM_SET))
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
            "simState": ("sim_state", listing.as_str),
            "simType": ("sim_type", listing.as_str),
            "batchNumber": ("sim_batch", listing.as_str),
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
    sent_kind = (server.get("resourceSpecification") or {}).get("id")
    kind = sent_kind if sent_kind in KINDS else "msisdn"
    data, documents = parse_documented(
        ResourceCreate, body, DOCUMENTS, API, "LogicalResource_FVO", found=problems
    )
    check_value(kind, data.value)
    iccid, documents = split_iccid(kind, documents)
    with tx() as conn:
        claim_id(conn, "subscriber_number", uid)
        # The unique constraint also protects callers outside this API. This lock gives
        # concurrent API duplicates a useful 409 rather than an aborted transaction.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"subscriber_number:value:{kind}:{data.value}",),
        )
        if conn.execute(
            "SELECT 1 FROM subscriber_number WHERE resource_specification_id = %s AND value = %s",
            (kind, data.value),
        ).fetchone():
            raise conflict("a resource with this value already exists")
        if (
            iccid is not None
            and conn.execute(
                "SELECT 1 FROM subscriber_number WHERE iccid = %s", (iccid,)
            ).fetchone()
        ):
            raise conflict("a resource with this iccid already exists")
        now = clock.now()
        values = {
            "name": data.name,
            "value": data.value,
            "resource_specification_id": kind,
            "iccid": iccid,
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
    check_type(body, "LogicalResource", "PhysicalResource")  # a physical SIM: see below
    server, problems = server_values(body)
    # TMF639's PATCH uses Resource, including LogicalResource properties.
    data, documents = parse_documented(
        ResourcePatch, body, DOCUMENTS, API, "LogicalResource", found=problems
    )
    with tx() as conn:
        uid = parse_id("resource", resource_id)
        row = require(conn, uid, lock=True)
        if body.get("@type") == "PhysicalResource" and row["sim_type"] != "physical":
            raise bad_request("@type is read-only (this resource is a LogicalResource)")
        today, now = clock.today(), clock.now()
        before = resource_body(conn, row, today)
        check_server_set(server, before)
        not_null(data, "name", "value", "administrative_state")
        if "value" in data.model_fields_set and data.value != row["value"]:
            raise conflict("number value is immutable")
        documents = drop_derived(row, before, documents)
        iccid, documents = split_iccid(row["resource_specification_id"], documents)
        iccid_change = {}
        if iccid is not None and iccid != row["iccid"]:
            if conn.execute(
                "SELECT 1 FROM number_assignment WHERE subscriber_number_id = %s LIMIT 1", (uid,)
            ).fetchone():
                raise conflict("iccid cannot change once the IMSI has been assigned")
            if conn.execute(
                "SELECT 1 FROM subscriber_number WHERE iccid = %s AND id <> %s", (iccid, uid)
            ).fetchone():
                raise conflict("a resource with this iccid already exists")
            iccid_change = {"iccid": iccid}
        old = emit.before(conn, API, "resource", lambda: before)
        changes = {
            key: getattr(data, key)
            for key in ("name", "description", "administrative_state")
            if key in data.model_fields_set
        }
        changes |= iccid_change | {
            "attrs": jsonb(merge_documents(row["attrs"], documents)),
            "tmf_meta": metadata(data, row["tmf_meta"]),
            "updated_at": now,
        }
        updated = repo.update(conn, uid, changes)
        if data.administrative_state == "unlocked" and row["administrative_state"] == "locked":
            sims.unlock_state(conn, uid, today, now)  # a retired SIM is stocked again
            updated = repo.get(conn, uid)
        new = resource_body(conn, updated, today)
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
