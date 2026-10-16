"""TMF620 categories and product catalogs; references connect to existing offerings."""

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.catalog import (
    BASE,
    LifecycleStatus,
    _check_lifecycle,
    delete_row,
    refuse_if_referenced,
)
from app.api.common import (
    ProductOfferingRef,
    Ref,
    TimePeriod,
    TmfModel,
    check_type,
    columns,
    created,
    not_null,
    parse,
    parse_id,
    ref,
    time_period,
    utc,
    without_nulls,
)
from app.api.deps import JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
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
    )
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
) -> dict[str, Any]:
    """Validate against serialized state, then replace scalar fields and memberships."""
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
    row = repo.update(conn, table, uid, values) if current else repo.create(conn, table, values)
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


@router.post("/category")
def create_category(body: JsonBody, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATEGORY_ATTRIBUTES)
    check_type(body, "Category")
    data = parse(CategoryCreate, body, read_only=("subCategory",))
    with tx() as conn:
        return created(listing.select_fields(save(conn, data, True), fields))


@router.patch("/category/{category_id}")
def patch_category(
    category_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATEGORY_ATTRIBUTES)
    check_type(body, "Category")
    data = parse(CategoryPatch, body, read_only=("lastUpdate", "subCategory"))
    with tx() as conn:
        return TmfJSONResponse(
            listing.select_fields(save(conn, data, True, parse_id("category", category_id)), fields)
        )


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
def create_catalog(body: JsonBody, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATALOG_ATTRIBUTES)
    check_type(body, "ProductCatalog")
    data = parse(CatalogCreate, body)
    with tx() as conn:
        return created(listing.select_fields(save(conn, data, False), fields))


@router.patch("/productCatalog/{catalog_id}")
def patch_catalog(
    catalog_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CATALOG_ATTRIBUTES)
    check_type(body, "ProductCatalog")
    data = parse(CatalogPatch, body, read_only=("lastUpdate",))
    with tx() as conn:
        return TmfJSONResponse(
            listing.select_fields(
                save(conn, data, False, parse_id("productCatalog", catalog_id)), fields
            )
        )


@router.delete("/category/{category_id}", status_code=204, response_class=Response)
def delete_category(category_id: str, request: Request, tx: Tx) -> Response:
    """Only an empty category: no subcategories, offerings or catalogs referring to it."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("category", category_id)
        repo.lock(conn)
        if repo.get(conn, "category", uid) is None:
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
        delete_row(conn, "category", uid)
    return Response(status_code=204)


@router.delete("/productCatalog/{catalog_id}", status_code=204, response_class=Response)
def delete_catalog(catalog_id: str, request: Request, tx: Tx) -> Response:
    """Nothing refers to a catalog; its root categories stay, only the membership goes."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productCatalog", catalog_id)
        repo.lock(conn)
        if repo.get(conn, "product_catalog", uid) is None:
            raise not_found("productCatalog", catalog_id)
        repo.remove_memberships(conn, "catalog", uid)
        delete_row(conn, "product_catalog", uid)
    return Response(status_code=204)
