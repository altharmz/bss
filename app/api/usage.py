"""TMF635 v5 product usage: the network's usage records. docs/decisions/usage.md.

GET list and retrieve, and POST (a mediation device recording one usage on a named product;
idempotent by its externalIdentifier). A usage is a fact the network reported: PATCH and
DELETE are 405, rating is a later slice, `exportJob` and `/hub` are not implemented (the
spec's only events are the export job's). The network's own feed reaches the same records
through the billing worker (app/worker/usage.py).
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from app.api import listing
from app.api.common import (
    ProductRef,
    TmfModel,
    billing_tz,
    check_type,
    created,
    documents_model,
    parse_documented,
    parse_id,
    ref,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.inventory import product_href
from app.api.tmfjson import TmfJSONResponse
from app.network import usage as rules
from app.repository import usage as repo
from app.repository._sql import Row, fetch_one

API = "TMF635"
BASE = "/tmf-api/productUsageManagement/v5"
router = APIRouter(prefix=BASE)
KINDS = repo.KINDS
OWNER = "network"
ID_TYPE = "networkRecordId"
DOCUMENTS = ("usageCharacteristic", "externalIdentifier")
# in the spec, but usage is reported by the network: nothing of these is held
UNSUPPORTED = ("serviceUsage", "relatedParty", "usageProductPrice")
ATTRIBUTES = (
    "creationDate",
    "lastUpdate",
    "status",
    "usageType",
    "usageCharacteristic",
    "usageProductPrice",
    "product",
    "usageSpecification",
    "externalIdentifier",
)
UsageDocuments = documents_model("UsageDocuments", API, (*DOCUMENTS, *UNSUPPORTED))


class UsageSpecificationRef(TmfModel):
    id: str
    href: str | None = None
    name: str | None = None
    version: str | None = None
    at_referred_type: str | None = Field(default=None, alias="@referredType")


class UsageCreate(UsageDocuments):
    usage_specification: UsageSpecificationRef
    product: ProductRef
    usage_type: str | None = None
    last_update: datetime | None = None


def usage_href(usage_id: Any) -> str:
    return f"{BASE}/productUsage/{usage_id}"


def characteristic(kind: str, name: str, value: Any) -> dict[str, Any]:
    return {
        "@type": kind,
        "name": name,
        "valueType": kind.removesuffix("Characteristic").lower(),
    } | {"value": value}


def usage_body(conn, row: Row) -> dict[str, Any]:
    product = (
        fetch_one(conn, "SELECT id, name FROM product WHERE id = %s", (row["product_id"],))
        if row["product_id"] is not None
        else None
    )
    traits = [
        characteristic("IntegerCharacteristic", "quantity", row["quantity"]),
        characteristic("StringCharacteristic", "unit", rules.UNITS[row["kind"]]),
        characteristic("StringCharacteristic", "eventTime", utc(row["event_at"]).isoformat()),
        characteristic("BooleanCharacteristic", "roaming", row["roaming"]),
        characteristic("BooleanCharacteristic", "throttled", row["throttled"]),
    ]
    for name in ("msisdn", "imsi"):
        if row[name] is not None:
            traits.append(characteristic("StringCharacteristic", name, row[name]))
    if row["rejection_reason"] is not None:
        traits.append(
            characteristic("StringCharacteristic", "rejectionReason", row["rejection_reason"])
        )
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": usage_href(row["id"]),
            "@type": "ProductUsage",
            "creationDate": utc(row["created_at"]),
            "lastUpdate": utc(row["created_at"]),
            "status": row["status"],
            "usageType": row["kind"],
            "usageCharacteristic": traits,
            "usageSpecification": {
                "id": row["kind"],
                "name": row["kind"],
                "@type": "ProductUsageSpecificationRef",
            },
            "product": None
            if product is None
            else ref(
                "Product",
                product_href(product["id"]),
                product["id"],
                product["name"],
                ref_type="ProductRef",
            ),
            "externalIdentifier": [
                {
                    "@type": "ExternalIdentifier",
                    "owner": OWNER,
                    "externalIdentifierType": ID_TYPE,
                    "id": row["network_record_id"],
                }
            ],
        }
    )


FILTERS = {
    "id": ("id", listing.as_uuid),
    "status": ("status", listing.as_str),
    "usageType": ("usage_type", listing.as_str),
    "product.id": ("product_id", listing.as_uuid),
}


@router.get("/productUsage")
def list_usage(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=FILTERS, attributes=ATTRIBUTES)
    with tx() as conn:
        rows, total = repo.page(conn, query.where, query.offset, query.limit)
        bodies = [usage_body(conn, row) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get("/productUsage/{usage_id}")
def get_usage(usage_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id("productUsage", usage_id))
        if row is None:
            raise not_found("productUsage", usage_id)
        return TmfJSONResponse(listing.select_fields(usage_body(conn, row), fields))


def _characteristics(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """The usage characteristics of a POST by name, with their type checked."""
    found: dict[str, Any] = {}
    for item in documents:
        name = item.get("name")
        if name in found:
            raise unprocessable(f"usageCharacteristic {name} is given twice")
        found[name] = item.get("value")
    unknown = sorted(
        set(found) - {"quantity", "eventTime", "msisdn", "imsi", "roaming", "throttled"}
    )
    if unknown:
        raise unprocessable(f"usageCharacteristic: unknown name(s) {', '.join(unknown)}")
    quantity = found.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise unprocessable("usageCharacteristic quantity is required: a positive integer")
    for name in ("roaming", "throttled"):
        if name in found and not isinstance(found[name], bool):
            raise unprocessable(f"usageCharacteristic {name} must be true or false")
    for name in ("msisdn", "imsi", "eventTime"):
        if name in found and not isinstance(found[name], str):
            raise unprocessable(f"usageCharacteristic {name} must be a string")
    return found


@router.post("/productUsage")
def create_usage(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    listing.retrieve_fields(request, ATTRIBUTES)  # `fields` is the only query parameter
    check_type(body, "ProductUsage")
    data, documents = parse_documented(
        UsageCreate, body, (*DOCUMENTS, *UNSUPPORTED), API, "ProductUsage_FVO"
    )
    for name in UNSUPPORTED:
        if documents.get(name) is not None:
            raise unprocessable(f"{name} is not supported: usage is reported by the network")
    kind = data.usage_specification.id
    if kind not in KINDS:
        raise unprocessable(f"usageSpecification.id must be one of {', '.join(KINDS)}")
    if data.usage_type is not None and data.usage_type != kind:
        raise unprocessable("usageType, when sent, must be the usageSpecification id")
    identifiers = documents.get("externalIdentifier") or []
    if len(identifiers) != 1:
        raise unprocessable("exactly one externalIdentifier (the network's record id) is required")
    record_id = identifiers[0]["id"]
    traits = _characteristics(documents.get("usageCharacteristic") or [])
    try:
        event_at = (
            datetime.fromisoformat(traits["eventTime"]) if "eventTime" in traits else clock.now()
        )
    except ValueError:
        raise unprocessable("usageCharacteristic eventTime must be a date-time") from None
    if event_at.tzinfo is None:
        raise unprocessable("usageCharacteristic eventTime needs a time zone")
    with tx() as conn:
        product = fetch_one(
            conn,
            "SELECT id, billing_account_id FROM product WHERE id = %s",
            (data.product.id,),
        )
        if product is None:
            raise unprocessable(f"product {data.product.id} does not exist")
        existing = repo.by_network_id(conn, record_id)
        if existing is not None:
            if str(existing["product_id"]) != str(product["id"]) or existing["kind"] != kind:
                raise conflict(f"externalIdentifier {record_id} names another usage")
            return created(usage_body(conn, existing))
        row, _ = rules.ingest(
            conn,
            rules.Incoming(
                network_record_id=record_id,
                kind=kind,
                quantity=traits["quantity"],
                event_at=event_at,
                msisdn=traits.get("msisdn"),
                imsi=traits.get("imsi"),
                roaming=traits.get("roaming", False),
                throttled=traits.get("throttled", False),
                product_id=product["id"],
            ),
            now=clock.now(),
            tz=billing_tz(),
        )
        response = created(usage_body(conn, row))
    return response
