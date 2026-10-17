"""TMF620 categories and product catalogs; references connect to existing offerings."""

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.catalog import (
    API,
    BASE,
    LifecycleStatus,
    _check_lifecycle,
)
from app.api.common import (
    ProductOfferingRef,
    Ref,
    TimePeriod,
    TmfModel,
    check_server_set,
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
    take_server_set,
    time_period,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import classification as repo
from app.repository._sql import Row, count_references, select_page

router = APIRouter(prefix=BASE)


class CategoryRef(Ref):
    referred_type = "Category"


class CatalogEntityCreate(TmfModel):
    name: str = Field(min_length=1)
    description: str | None = None
    lifecycle_status: LifecycleStatus = "In Design"
    valid_for: TimePeriod | None = None
    version: str | None = None
    last_update: Any = None


class CatalogEntityPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None
    version: str | None = None


class CategoryCreate(CatalogEntityCreate):
    parent: CategoryRef | None = None
    is_root: bool | None = None
    product_offering: list[ProductOfferingRef] = []


class CategoryPatch(CatalogEntityPatch):
    parent: CategoryRef | None = None
    is_root: bool | None = None
    product_offering: list[ProductOfferingRef] | None = None


class CatalogCreate(CatalogEntityCreate):
    category: list[CategoryRef] = []
    catalog_type: str | None = None


class CatalogPatch(CatalogEntityPatch):
    category: list[CategoryRef] | None = None
    catalog_type: str | None = None


COMMON = ("name", "description", "lifecycleStatus", "validFor", "version", "lastUpdate")
CATEGORY_ATTRIBUTES = (*COMMON, "parent", "isRoot", "subCategory", "productOffering")
CATALOG_ATTRIBUTES = (*COMMON, "catalogType", "category")


def references(resource: str, rows: list[Row]) -> list[dict[str, Any]]:
    return [
        ref(resource[0].upper() + resource[1:], f"{BASE}/{resource}/{r['id']}", r["id"], r["name"])
        for r in rows
    ]


def organization_body(conn: psycopg.Connection, row: Row, category: bool) -> dict[str, Any]:
    resource = "category" if category else "productCatalog"
    body = without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/{resource}/{row['id']}",
            "@type": "Category" if category else "ProductCatalog",
            "name": row["name"],
            "description": row["description"],
            "lifecycleStatus": row["lifecycle_status"],
            "version": row["version"],
            "validFor": time_period(row["valid_for"]) if row["valid_for"] is not None else None,
            "lastUpdate": utc(row["updated_at"]),
        }
    ) | meta_body(row)
    if category:
        body["isRoot"] = row["parent_id"] is None
        if row["parent_id"] is not None:
            parent = repo.get(conn, "category", row["parent_id"])
            assert parent is not None  # foreign key
            body["parent"] = references("category", [parent])[0]
        body["subCategory"] = references(
            "category", repo.related(conn, "category", "parent_id", row["id"])
        )
        body["productOffering"] = references(
            "productOffering", repo.memberships(conn, "offering", row["id"], reverse=True)
        )
    else:
        body["category"] = references("category", repo.memberships(conn, "catalog", row["id"]))
        if row["catalog_type"] is not None:
            body["catalogType"] = row["catalog_type"]
    return body


def validate_refs(
    conn: psycopg.Connection, refs: list[Ref], table: repo.Table, *, roots: bool = False
) -> list[UUID]:
    ids = [r.id for r in refs]
    if len(set(ids)) != len(ids):
        raise unprocessable("duplicate references are not allowed")
    for uid in ids:
        row = repo.get(conn, table, uid)
        if row is None:
            raise unprocessable(f"{table} {uid} does not exist")
        if roots and row["parent_id"] is not None:
            raise conflict("a product catalog may reference only root categories")
    return ids


def save(
    conn: psycopg.Connection,
    data: CategoryCreate | CategoryPatch | CatalogCreate | CatalogPatch,
    category: bool,
    uid: UUID | None = None,
    new_id: UUID | None = None,
) -> dict[str, Any]:
    """Validate against serialized state, then replace scalar fields and memberships.
    `uid`: the entry to PATCH; `new_id`: the id of a new one (an import's source id)."""
    repo.lock(conn)
    table = "category" if category else "product_catalog"
    current = repo.get(conn, table, uid) if uid else None
    if uid and current is None:
        raise not_found("category" if category else "productCatalog", str(uid))
    if current:
        not_null(
            data,
            "name",
            "lifecycle_status",
            "product_offering" if category else "category",
            *(["is_root"] if category else []),
        )
        if data.lifecycle_status is not None:
            _check_lifecycle(table, current["lifecycle_status"], data.lifecycle_status)
    values = columns(
        data,
        exclude=frozenset(
            {"last_update", "valid_for", "parent", "is_root", "product_offering", "category"}
        ),
    )
    if "valid_for" in data.model_fields_set:
        values["valid_for"] = data.valid_for.to_range() if data.valid_for else None
    if category:
        parent_id = current["parent_id"] if current else None
        if "parent" in data.model_fields_set:
            parent_id = data.parent.id if data.parent else None
            if parent_id:
                validate_refs(conn, [data.parent], "category")
                if uid and uid in {r["id"] for r in repo.ancestors(conn, parent_id)}:
                    raise conflict("category hierarchy cannot contain a cycle")
            if parent_id and uid and repo.memberships(conn, "catalog", uid, reverse=True):
                raise conflict(
                    "remove the root category from its catalogs before assigning a parent"
                )
        if data.is_root is not None and data.is_root != (parent_id is None):
            raise unprocessable(
                "isRoot must agree with parent (true exactly when parent is absent)"
            )
        values["parent_id"] = parent_id
        refs = data.product_offering
        ids = validate_refs(conn, refs, "product_offering") if refs is not None else None
    else:
        refs = data.category
        ids = validate_refs(conn, refs, "category", roots=True) if refs is not None else None
    if current and ids is not None and not values:
        # Membership is part of this resource: fire its lastUpdate trigger too.
        values["name"] = current["name"]
    if not current and new_id is not None:
        values["id"] = new_id
    row = repo.update(conn, table, uid, values) if current else repo.create(conn, table, values)
    row = with_meta(conn, table, row, data)
    if ids is not None:
        repo.replace(conn, "offering" if category else "catalog", row["id"], ids, reverse=category)
    return organization_body(conn, row, category)


@router.get("/category")
def list_categories(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "lifecycleStatus": ("lifecycle_status", listing.as_str),
            "parent.id": ("parent_id", listing.as_uuid),
        },
        attributes=CATEGORY_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = select_page(
            conn, "category", where=query.where, offset=query.offset, limit=query.limit
        )
        items = [organization_body(conn, row, True) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/category/{category_id}")
def get_category(category_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATEGORY_ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, "category", parse_id("category", category_id))
        if row is None:
            raise not_found("category", category_id)
        return TmfJSONResponse(listing.select_fields(organization_body(conn, row, True), fields))


def _resource(category: bool) -> tuple[str, str]:
    return ("category", "category") if category else ("productCatalog", "product_catalog")


def _created(
    conn: psycopg.Connection, data, category: bool, at, new_id: UUID | None, server_set
) -> dict[str, Any]:
    claim_id(conn, _resource(category)[1], new_id)
    new = save(conn, data, category, new_id=new_id)
    check_server_set(server_set, new)
    emit.created(conn, API, _resource(category)[0], new, at=at)
    return new


def _patched(
    conn: psycopg.Connection, data, category: bool, uid: UUID, at, server_set
) -> dict[str, Any]:
    resource, table = _resource(category)
    current = repo.get(conn, table, uid)
    old = None
    if current is not None:  # else save answers the 404
        before = organization_body(conn, current, category)
        check_server_set(server_set, before)
        old = emit.before(conn, API, resource, lambda: before)
    new = save(conn, data, category, uid)
    emit.changed(conn, API, resource, old, new, at=at, state="lifecycleStatus")
    return new


@router.post("/category")
def create_category(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATEGORY_ATTRIBUTES)
    check_type(body, "Category")
    uid = take_id(body)
    server_set = take_server_set(body, ("subCategory",))
    data = parse(CategoryCreate, body)
    with tx() as conn:
        new = _created(conn, data, True, clock.now(), uid, server_set)
    return created(listing.select_fields(new, fields))


@router.patch("/category/{category_id}")
def patch_category(
    category_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATEGORY_ATTRIBUTES)
    check_type(body, "Category")
    server_set = take_server_set(body, ("lastUpdate", "subCategory"))
    data = parse(CategoryPatch, body)
    uid = parse_id("category", category_id)
    with tx() as conn:
        patched = _patched(conn, data, True, uid, clock.now(), server_set)
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.get("/productCatalog")
def list_catalogs(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "lifecycleStatus": ("lifecycle_status", listing.as_str),
            "catalogType": ("catalog_type", listing.as_str),
        },
        attributes=CATALOG_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = select_page(
            conn, "product_catalog", where=query.where, offset=query.offset, limit=query.limit
        )
        items = [organization_body(conn, row, False) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/productCatalog/{catalog_id}")
def get_catalog(catalog_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATALOG_ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, "product_catalog", parse_id("productCatalog", catalog_id))
        if row is None:
            raise not_found("productCatalog", catalog_id)
        return TmfJSONResponse(listing.select_fields(organization_body(conn, row, False), fields))


@router.post("/productCatalog")
def create_catalog(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATALOG_ATTRIBUTES)
    check_type(body, "ProductCatalog")
    uid = take_id(body)
    data = parse(CatalogCreate, body)
    with tx() as conn:
        new = _created(conn, data, False, clock.now(), uid, {})
    return created(listing.select_fields(new, fields))


@router.patch("/productCatalog/{catalog_id}")
def patch_catalog(
    catalog_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATALOG_ATTRIBUTES)
    check_type(body, "ProductCatalog")
    server_set = take_server_set(body, ("lastUpdate",))
    data = parse(CatalogPatch, body)
    uid = parse_id("productCatalog", catalog_id)
    with tx() as conn:
        patched = _patched(conn, data, False, uid, clock.now(), server_set)
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/category/{category_id}", status_code=204, response_class=Response)
def delete_category(category_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only an empty category: no subcategories, offerings or catalogs referring to it."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("category", category_id)
        repo.lock(conn)
        row = repo.get(conn, "category", uid)
        if row is None:
            raise not_found("category", category_id)
        counts = count_references(
            conn,
            uid,
            {
                "subCategory": ("category", "parent_id"),
                "productOffering": ("offering_category", "category_id"),
                "productCatalog": ("catalog_category", "category_id"),
            },
        )
        refuse_if_referenced("category", uid, counts, "remove them from it first")
        emit.deleted(
            conn, API, "category", lambda: organization_body(conn, row, True), at=clock.now()
        )
        delete_row(conn, "category", uid)
    return Response(status_code=204)


@router.delete("/productCatalog/{catalog_id}", status_code=204, response_class=Response)
def delete_catalog(catalog_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Nothing refers to a catalog; its root categories stay, only the membership goes."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productCatalog", catalog_id)
        repo.lock(conn)
        row = repo.get(conn, "product_catalog", uid)
        if row is None:
            raise not_found("productCatalog", catalog_id)
        emit.deleted(
            conn, API, "productCatalog", lambda: organization_body(conn, row, False), at=clock.now()
        )
        repo.remove_memberships(conn, "catalog", uid)
        delete_row(conn, "product_catalog", uid)
    return Response(status_code=204)
