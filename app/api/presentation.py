"""TMF666 bill formats and presentation media used by billing accounts."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from psycopg.errors import UniqueViolation
from pydantic import Field

from app.api import listing
from app.api.common import (
    Ref,
    TmfModel,
    check_type,
    claim_id,
    columns,
    created,
    meta_body,
    not_null,
    parse,
    parse_id,
    ref,
    take_id,
    with_meta,
    without_nulls,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found
from app.api.roles import require_admin_or_service
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import presentation as repo
from app.repository._sql import count_references

API = "TMF666"
BASE = "/tmf-api/accountManagement/v5"
router = APIRouter(prefix=BASE)
# billing settings writes are admin-only (#467); the reads feed the account Edit form
ADMIN = [Depends(require_admin_or_service)]
ATTRIBUTES = ("name", "description")


class FormatRef(Ref):
    referred_type = "BillFormat"


class MediaRef(Ref):
    referred_type = "BillPresentationMedia"


class CycleRef(TmfModel):
    """A billingCycleSpecification: `monthly` (built in) or a stored one's id."""

    id: str = Field(min_length=1)
    href: str | None = None
    name: str | None = None
    at_referred_type: Literal["BillingCycleSpecification"] | None = Field(
        default=None, alias="@referredType"
    )


class BillStructure(TmfModel):
    format: FormatRef | None = None
    presentation_media: list[MediaRef] | None = None
    # null (or monthly) = the built-in specification
    cycle_specification: CycleRef | None = None


class MediaCreate(TmfModel):
    name: str = Field(min_length=1)
    description: str | None = None


class MediaPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


class FormatCreate(MediaCreate):
    name: Literal["JSON detailed", "JSON summary"]


class FormatPatch(MediaPatch):
    name: Literal["JSON detailed", "JSON summary"] | None = None


def resource_body(row, resource):
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/{resource}/{row['id']}",
            "@type": resource[0].upper() + resource[1:],
            "name": row["name"],
            "description": row["description"],
        }
    ) | meta_body(row)


def structure_from(pref):
    """A bill structure from an account's preferences (repo.preferences / preferences_of)."""
    body = {}
    if pref["format"]:
        f = pref["format"]
        body["format"] = ref("BillFormat", f"{BASE}/billFormat/{f['id']}", f["id"], f["name"])
    if pref["media"]:
        body["presentationMedia"] = [
            ref(
                "BillPresentationMedia",
                f"{BASE}/billPresentationMedia/{m['id']}",
                m["id"],
                m["name"],
            )
            for m in pref["media"]
        ]
    return body


def save(conn, data, table, resource, uid=None, *, at, new_id=None):
    """`uid`: the one to change; else a new one (`new_id`: chosen by the client)."""
    old = None
    claim_id(conn, table, new_id)
    if uid:
        row = repo.get(conn, table, uid)
        if row is None:
            raise not_found(resource, str(uid))
        not_null(data, "name")
        current = row
        old = emit.before(conn, API, resource, lambda: resource_body(current, resource))
    try:
        with conn.transaction():
            row = (
                repo.update(conn, table, uid, columns(data))
                if uid
                else repo.create(conn, table, data, new_id)
            )
    except UniqueViolation:
        raise conflict("presentation name already exists") from None
    new = resource_body(with_meta(conn, table, row, data), resource)
    if uid:
        emit.changed(conn, API, resource, old, new, at=at)
    else:
        emit.created(conn, API, resource, new, at=at)
    return new


@router.get("/billFormat")
def list_bill_formats(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request, filters={"name": ("name", listing.as_str)}, attributes=ATTRIBUTES
    )
    with tx() as conn:
        rows, more = repo.page(
            conn, "bill_format", where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([resource_body(r, "billFormat") for r in rows], more, query)


@router.get("/billFormat/{format_id}")
def get_bill_format(format_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, "bill_format", parse_id("billFormat", format_id))
        if row is None:
            raise not_found("billFormat", format_id)
    return TmfJSONResponse(listing.select_fields(resource_body(row, "billFormat"), fields))


@router.post("/billFormat", dependencies=ADMIN)
def create_bill_format(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "BillFormat")
    new_id = take_id(body)
    data = parse(FormatCreate, body)
    with tx() as conn:
        new = save(conn, data, "bill_format", "billFormat", at=clock.now(), new_id=new_id)
    return created(listing.select_fields(new, fields))


@router.patch("/billFormat/{format_id}", dependencies=ADMIN)
def patch_bill_format(
    format_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "BillFormat")
    data = parse(FormatPatch, body)
    uid = parse_id("billFormat", format_id)
    with tx() as conn:
        patched = save(conn, data, "bill_format", "billFormat", uid, at=clock.now())
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.get("/billPresentationMedia")
def list_bill_media(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request, filters={"name": ("name", listing.as_str)}, attributes=ATTRIBUTES
    )
    with tx() as conn:
        rows, more = repo.page(
            conn,
            "bill_presentation_media",
            where=query.where,
            offset=query.offset,
            limit=query.limit,
        )
    return listing.list_response(
        [resource_body(r, "billPresentationMedia") for r in rows], more, query
    )


@router.get("/billPresentationMedia/{media_id}")
def get_bill_media(media_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, "bill_presentation_media", parse_id("billPresentationMedia", media_id))
        if row is None:
            raise not_found("billPresentationMedia", media_id)
    return TmfJSONResponse(
        listing.select_fields(resource_body(row, "billPresentationMedia"), fields)
    )


@router.post("/billPresentationMedia", dependencies=ADMIN)
def create_bill_media(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "BillPresentationMedia")
    new_id = take_id(body)
    data = parse(MediaCreate, body)
    with tx() as conn:
        new = save(
            conn,
            data,
            "bill_presentation_media",
            "billPresentationMedia",
            at=clock.now(),
            new_id=new_id,
        )
    return created(listing.select_fields(new, fields))


@router.patch("/billPresentationMedia/{media_id}", dependencies=ADMIN)
def patch_bill_media(
    media_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "BillPresentationMedia")
    data = parse(MediaPatch, body)
    uid = parse_id("billPresentationMedia", media_id)
    with tx() as conn:
        patched = save(
            conn, data, "bill_presentation_media", "billPresentationMedia", uid, at=clock.now()
        )
    return TmfJSONResponse(listing.select_fields(patched, fields))


# --- deletes: only what no billing account prefers (issued bills keep a snapshot) ---


def _delete(
    request: Request, tx: Tx, clock: ClockDep, table: str, resource: str, raw_id: str, refs
) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(resource, raw_id)
        row = repo.get(conn, table, uid, "update")
        if row is None:
            raise not_found(resource, raw_id)
        counts = count_references(conn, uid, {"billingAccount": refs})
        refuse_if_referenced(resource, uid, counts, "change their billStructure first")
        emit.deleted(conn, API, resource, resource_body(row, resource), at=clock.now())
        delete_row(conn, table, uid)
    return Response(status_code=204)


@router.delete(
    "/billFormat/{format_id}", status_code=204, response_class=Response, dependencies=ADMIN
)
def delete_bill_format(format_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(
        request,
        tx,
        clock,
        "bill_format",
        "billFormat",
        format_id,
        ("billing_account", "bill_format_id"),
    )


@router.delete(
    "/billPresentationMedia/{media_id}",
    status_code=204,
    response_class=Response,
    dependencies=ADMIN,
)
def delete_bill_media(media_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete(
        request,
        tx,
        clock,
        "bill_presentation_media",
        "billPresentationMedia",
        media_id,
        ("account_presentation_media", "media_id"),
    )
