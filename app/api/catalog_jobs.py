"""TMF620 importJob and exportJob: the catalog as one JSON document, carried as a
data: URL (`data:application/json;base64,...`).

The server never fetches or writes a remote location: an export's `url` comes back as
the document itself, and an import's `url` must be such a document - the output of an
export, here or on another instance. Both jobs run synchronously in their request
(catalogs are small), so a stored job is finished: `Succeeded` or `Failed`.

An import applies POST's rules (references, prices never starting in the past, codes,
hierarchy) and keeps the source's ids, all or nothing. An entry whose id exists with
the same content is skipped, so importing a document twice is a no-op; with different
content the job fails. A failed job is stored with its `errorLog` (the reason, as text)
and nothing of the document is written, like a rejected order. See
docs/decisions/catalog.md ("Import and export jobs").
"""

import base64
import binascii
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing, tmfjson
from app.api.catalog import (
    BASE,
    OfferingCreate,
    PriceCreate,
    SpecificationCreate,
    insert_offering,
    insert_price,
    insert_specification,
    offering_body,
    price_body,
    specification_body,
)
from app.api.classification import CatalogCreate, CategoryCreate, organization_body, save
from app.api.common import TmfModel, check_type, created, parse, parse_id, utc, without_nulls
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.repository import catalog, catalog_jobs, classification
from app.repository._sql import Row, fetch_all

router = APIRouter(prefix=BASE)

CONTENT_TYPE = "application/json"
DATA_PREFIX = f"data:{CONTENT_TYPE};base64,"
DOCUMENT_TYPE = "BssCatalogDocument"
DOCUMENT_VERSION = 1
MAX_DOCUMENT_BYTES = 1_000_000  # of the JSON document, before base64
# Import order: everything an entry references comes before it.
KINDS = ("productSpecification", "productOfferingPrice", "productOffering", "category",
         "productCatalog")  # fmt: skip
TYPES = {
    "productSpecification": "ProductSpecification",
    "productOfferingPrice": "ProductOfferingPrice",
    "productOffering": "ProductOffering",
    "category": "Category",
    "productCatalog": "ProductCatalog",
}
# Attributes a body carries that a POST does not take: server-managed or derived.
NOT_INPUT = {
    "productSpecification": {"id", "href"},
    "productOfferingPrice": {"id", "href"},
    "productOffering": {"id", "href", "category"},
    "category": {"id", "href", "subCategory"},
    "productCatalog": {"id", "href"},
}
# Left out when an existing entry is compared with the document's: they change without
# the entry itself changing (lastUpdate) or are derived from other entries.
NOT_COMPARED = {
    "productSpecification": {"lastUpdate"},
    "productOfferingPrice": {"lastUpdate"},
    "productOffering": {"lastUpdate", "category"},
    "category": {"lastUpdate", "subCategory"},
    "productCatalog": {"lastUpdate"},
}
JOB_ATTRIBUTES = (
    "status",
    "contentType",
    "path",
    "url",
    "creationDate",
    "completionDate",
    "errorLog",
)
EXPORT_ATTRIBUTES = (*JOB_ATTRIBUTES, "query")
SERVER_MANAGED = ("status", "creationDate", "completionDate", "errorLog")


class ImportProblem(Exception):
    """Why the document cannot be imported; the job fails with it as errorLog."""


class ImportJobCreate(TmfModel):
    url: str = Field(min_length=1)
    content_type: Literal["application/json"] = CONTENT_TYPE
    path: str | None = None


class ExportJobCreate(TmfModel):
    # The spec's _FVO requires url, the file to write. Ours is the document itself, so
    # only the inline form is accepted: the server writes nowhere else.
    url: str | None = None
    content_type: Literal["application/json"] = CONTENT_TYPE
    path: str | None = None
    query: str | None = None


def _check_path(path: str | None) -> None:
    if path not in (None, BASE):
        raise unprocessable(f"path must be {BASE}, the root of this catalog")


def job_body(row: Row) -> dict[str, Any]:
    resource = "importJob" if row["kind"] == "ImportJob" else "exportJob"
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/{resource}/{row['id']}",
            "@type": row["kind"],
            "status": row["status"],
            "contentType": row["content_type"],
            "path": row["path"],
            "query": row["query"],
            "url": row["url"],
            "creationDate": utc(row["creation_date"]),
            "completionDate": utc(row["completion_date"]),
            "errorLog": row["error_log"],
        }
    )


def _store(
    conn: psycopg.Connection,
    kind: catalog_jobs.Kind,
    now: datetime,
    *,
    url: str | None,
    query: str | None = None,
    error: str | None = None,
) -> Row:
    return catalog_jobs.insert(
        conn,
        {
            "kind": kind,
            "status": "Failed" if error else "Succeeded",
            "content_type": CONTENT_TYPE,
            "path": BASE,
            "query": query,
            "url": url,
            "error_log": error,
            "creation_date": now,
            "completion_date": now,
        },
    )


# --- export ---


def _scope(query: str | None) -> UUID | None:
    """None: the whole catalog; else the product catalog the query names."""
    if query in (None, ""):
        return None
    name, _, value = query.partition("=")
    if name != "productCatalog.id" or not value:
        raise unprocessable("query must be empty or productCatalog.id=<id>")
    try:
        return UUID(value)
    except ValueError:
        raise unprocessable(f"query: {value} is not a product catalog id") from None


def _rows(conn: psycopg.Connection, query: str, *params: Any) -> list[Row]:
    return fetch_all(conn, query, params)


def export_document(conn: psycopg.Connection, catalog_id: UUID | None) -> dict[str, Any]:
    """The catalog (or one product catalog with everything it reaches) as a document of
    full TMF620 bodies, in import order."""
    if catalog_id is None:
        everything = "SELECT * FROM {} ORDER BY created_at, id"
        specs = _rows(conn, everything.format("product_specification"))
        prices = _rows(conn, everything.format("product_offering_price"))
        offerings = _rows(conn, everything.format("product_offering"))
        categories = _rows(conn, everything.format("category"))
        catalogs = _rows(conn, everything.format("product_catalog"))
    else:
        found = classification.get(conn, "product_catalog", catalog_id)
        if found is None:
            raise unprocessable(f"query: productCatalog {catalog_id} does not exist")
        catalogs = [found]
        categories = _rows(
            conn,
            """
            WITH RECURSIVE tree AS (
                SELECT c.* FROM category c
                JOIN catalog_category m ON m.category_id = c.id WHERE m.catalog_id = %s
                UNION SELECT c.* FROM category c JOIN tree t ON c.parent_id = t.id
            ) SELECT * FROM tree ORDER BY created_at, id
            """,
            catalog_id,
        )
        offerings = _rows(
            conn,
            """
            SELECT DISTINCT o.* FROM product_offering o
            JOIN offering_category m ON m.offering_id = o.id
            WHERE m.category_id = ANY(%s) ORDER BY o.created_at, o.id
            """,
            [c["id"] for c in categories],
        )
        offering_ids = [o["id"] for o in offerings]
        specs = _rows(
            conn,
            "SELECT * FROM product_specification WHERE id = ANY(%s) ORDER BY created_at, id",
            list({o["product_specification_id"] for o in offerings}),
        )
        prices = _rows(
            conn,
            "SELECT * FROM product_offering_price WHERE product_offering_id = ANY(%s)"
            " ORDER BY created_at, id",
            offering_ids,
        )
    return {
        "@type": DOCUMENT_TYPE,
        "version": DOCUMENT_VERSION,
        "productSpecification": [specification_body(r) for r in specs],
        "productOfferingPrice": [price_body(r) for r in prices],
        "productOffering": [offering_body(conn, r) for r in offerings],
        "category": [organization_body(conn, r, True) for r in categories],
        "productCatalog": [organization_body(conn, r, False) for r in catalogs],
    }


def as_data_url(document: dict[str, Any]) -> str:
    raw = tmfjson.dumps(document).encode()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ImportProblem(f"the document is {len(raw)} bytes; at most {MAX_DOCUMENT_BYTES}")
    return DATA_PREFIX + base64.b64encode(raw).decode()


# --- import ---


def read_data_url(url: str) -> Any:
    """The JSON document of a data: URL, or a 422 if it cannot even be read."""
    if not url.startswith(DATA_PREFIX):
        raise unprocessable(
            f"url must be a {DATA_PREFIX}... URL (an export's url); the server fetches nothing"
        )
    encoded = url[len(DATA_PREFIX) :]
    if len(encoded) > (MAX_DOCUMENT_BYTES * 4) // 3 + 4:
        raise unprocessable(f"the document is larger than {MAX_DOCUMENT_BYTES} bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise unprocessable("url: the data is not valid base64") from None
    try:
        return tmfjson.loads(raw)
    except tmfjson.InvalidJSON as exc:
        raise unprocessable(f"url: the document is not JSON: {exc}") from None


def _current(conn: psycopg.Connection, kind: str, uid: UUID) -> dict[str, Any] | None:
    if kind == "productSpecification":
        row = catalog.get_product_specification(conn, uid)
        return specification_body(row) if row else None
    if kind == "productOfferingPrice":
        row = catalog.get_product_offering_price(conn, uid)
        return price_body(row) if row else None
    if kind == "productOffering":
        row = catalog.get_product_offering(conn, uid)
        return offering_body(conn, row) if row else None
    table = "category" if kind == "category" else "product_catalog"
    row = classification.get(conn, table, uid)
    return organization_body(conn, row, kind == "category") if row else None


def _create(
    conn: psycopg.Connection, kind: str, uid: UUID, body: dict[str, Any], now: datetime
) -> None:
    """POST's validation and write, under the source's id."""
    if kind == "productSpecification":
        insert_specification(conn, parse(SpecificationCreate, body), uid)
    elif kind == "productOfferingPrice":
        insert_price(conn, parse(PriceCreate, body), now, uid)
    elif kind == "productOffering":
        insert_offering(conn, parse(OfferingCreate, body), now, uid)
    elif kind == "category":
        save(conn, parse(CategoryCreate, body), True, new_id=uid)
    else:
        save(conn, parse(CatalogCreate, body), False, new_id=uid)


def _entries(document: dict[str, Any], kind: str) -> list[tuple[UUID, dict[str, Any]]]:
    entries = document.get(kind, [])
    if not isinstance(entries, list):
        raise ImportProblem(f"{kind} must be an array")
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ImportProblem(f"every {kind} entry must be an object with an id")
        try:
            uid = UUID(entry["id"])
        except ValueError:
            raise ImportProblem(f"{kind} {entry['id']}: the id is not a UUID") from None
        if entry.get("@type") != TYPES[kind]:
            raise ImportProblem(f"{kind} {uid}: @type must be {TYPES[kind]}")
        out.append((uid, entry))
    if len({uid for uid, _ in out}) != len(out):
        raise ImportProblem(f"{kind}: an id appears more than once")
    return out


def _parents_first(entries: list[tuple[UUID, dict[str, Any]]]) -> list[tuple[UUID, dict]]:
    """Categories ordered so a parent in the document comes before its children."""
    pending = dict(entries)
    ordered: list[tuple[UUID, dict]] = []
    while pending:
        ready = [
            uid
            for uid, entry in pending.items()
            if not isinstance(entry.get("parent"), dict)
            or UUID(str(entry["parent"].get("id"))) not in pending
        ]
        if not ready:
            raise ImportProblem("category: the parents form a cycle")
        for uid in ready:
            ordered.append((uid, pending.pop(uid)))
    return ordered


def import_document(conn: psycopg.Connection, document: Any, now: datetime) -> None:
    """Create every entry the document has and the catalog does not, under POST's rules.
    Raises ImportProblem; the caller rolls back whatever was written."""
    if not isinstance(document, dict) or document.get("@type") != DOCUMENT_TYPE:
        raise ImportProblem(f"the document must be a {DOCUMENT_TYPE} (an export's url)")
    if document.get("version") != DOCUMENT_VERSION:
        raise ImportProblem(f"unsupported document version (supported: {DOCUMENT_VERSION})")
    unknown = sorted(set(document) - {"@type", "version", *KINDS})
    if unknown:
        raise ImportProblem(f"unknown document attribute(s): {', '.join(unknown)}")
    classification.lock(conn)  # category and catalog writes
    for kind in KINDS:
        entries = _entries(document, kind)
        if kind == "category":
            try:
                entries = _parents_first(entries)
            except ValueError:
                raise ImportProblem("category: a parent id is not a UUID") from None
        for uid, entry in entries:
            current = _current(conn, kind, uid)
            if current is not None:
                current = tmfjson.loads(tmfjson.dumps(current).encode())  # as on the wire
                ignore = NOT_COMPARED[kind]
                differ = sorted(
                    k
                    for k in (set(current) | set(entry)) - ignore
                    if current.get(k) != entry.get(k)
                )
                if differ:
                    raise ImportProblem(
                        f"{kind} {uid} already exists with different content ({', '.join(differ)})"
                    )
                continue
            body = {k: v for k, v in entry.items() if k not in NOT_INPUT[kind]}
            try:
                _create(conn, kind, uid, body, now)
            except TmfError as exc:
                raise ImportProblem(f"{kind} {uid}: {exc.message}") from None


# --- routes ---


@router.get("/exportJob")
def list_export_jobs(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list(request, tx, "ExportJob", EXPORT_ATTRIBUTES)


@router.get("/exportJob/{job_id}")
def get_export_job(job_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get(job_id, request, tx, "ExportJob", EXPORT_ATTRIBUTES)


@router.post("/exportJob")
def create_export_job(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, EXPORT_ATTRIBUTES)
    check_type(body, "ExportJob")
    data = parse(ExportJobCreate, body, read_only=SERVER_MANAGED)
    _check_path(data.path)
    if data.url not in (None, f"data:{CONTENT_TYPE}"):
        raise unprocessable(
            f"url: the export is returned inline as a data: URL; send data:{CONTENT_TYPE} or"
            " nothing - the server writes nowhere else"
        )
    scope = _scope(data.query)
    now = clock.now()
    with tx() as conn:
        document = export_document(conn, scope)
        try:
            row = _store(
                conn, "ExportJob", now, url=as_data_url(document), query=data.query or None
            )
        except ImportProblem as exc:
            row = _store(conn, "ExportJob", now, url=None, query=data.query or None, error=str(exc))
    return created(listing.select_fields(job_body(row), fields))


@router.delete("/exportJob/{job_id}", status_code=204, response_class=Response)
def delete_export_job(job_id: str, request: Request, tx: Tx) -> Response:
    return _delete(job_id, request, tx, "ExportJob")


@router.get("/importJob")
def list_import_jobs(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list(request, tx, "ImportJob", JOB_ATTRIBUTES)


@router.get("/importJob/{job_id}")
def get_import_job(job_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get(job_id, request, tx, "ImportJob", JOB_ATTRIBUTES)


@router.post("/importJob")
def create_import_job(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, JOB_ATTRIBUTES)
    check_type(body, "ImportJob")
    data = parse(ImportJobCreate, body, read_only=SERVER_MANAGED)
    _check_path(data.path)
    document = read_data_url(data.url)
    now = clock.now()
    with tx() as conn:
        try:
            with conn.transaction():  # all or nothing
                import_document(conn, document, now)
            error = None
        except ImportProblem as exc:
            error = str(exc)
        row = _store(conn, "ImportJob", now, url=data.url, error=error)
    return created(listing.select_fields(job_body(row), fields))


@router.delete("/importJob/{job_id}", status_code=204, response_class=Response)
def delete_import_job(job_id: str, request: Request, tx: Tx) -> Response:
    """Removes the job record only; what it imported stays."""
    return _delete(job_id, request, tx, "ImportJob")


def _resource(kind: catalog_jobs.Kind) -> str:
    return "importJob" if kind == "ImportJob" else "exportJob"


def _list(request: Request, tx: Tx, kind: catalog_jobs.Kind, attributes) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request, filters={"status": ("status", listing.as_str)}, attributes=attributes
    )
    with tx() as conn:
        rows, total = catalog_jobs.page(
            conn, kind, where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([job_body(r) for r in rows], total, query)


def _get(job_id: str, request: Request, tx: Tx, kind: catalog_jobs.Kind, attributes):
    fields = listing.retrieve_fields(request, attributes)
    with tx() as conn:
        row = catalog_jobs.get(conn, kind, parse_id(_resource(kind), job_id))
    if row is None:
        raise not_found(_resource(kind), job_id)
    return TmfJSONResponse(listing.select_fields(job_body(row), fields))


def _delete(job_id: str, request: Request, tx: Tx, kind: catalog_jobs.Kind) -> Response:
    listing.no_query(request)
    with tx() as conn:
        if not catalog_jobs.delete(conn, kind, parse_id(_resource(kind), job_id)):
            raise not_found(_resource(kind), job_id)
    return Response(status_code=204)
