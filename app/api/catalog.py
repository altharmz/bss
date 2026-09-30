"""TMF620 Product Catalog Management v5: productSpecification, productOffering,
productOfferingPrice.

A price is created on its own and attached by the offering (productOfferingPrice[]), as
in the spec. A price belongs to at most one offering and is never detached or re-priced:
billing needs the historical price, so changing a price means ending its validFor and
attaching a new one.
"""

from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from psycopg import errors as pg_errors
from pydantic import Field, model_validator

from app.api import listing
from app.api.common import (
    Money,
    Ref,
    TimePeriod,
    TmfModel,
    check_type,
    money,
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
from app.repository import catalog
from app.repository._sql import Row

BASE = "/tmf-api/productCatalogManagement/v5"
router = APIRouter(prefix=BASE)

LifecycleStatus = Literal["In Design", "Active", "Retired"]

SPEC_ATTRIBUTES = ("name", "description", "lifecycleStatus", "lastUpdate")
OFFERING_ATTRIBUTES = (
    "name",
    "description",
    "lifecycleStatus",
    "isSellable",
    "validFor",
    "productSpecification",
    "productOfferingPrice",
    "lastUpdate",
)
PRICE_ATTRIBUTES = (
    "name",
    "lifecycleStatus",
    "priceType",
    "recurringChargePeriodType",
    "recurringChargePeriodLength",
    "price",
    "validFor",
    "lastUpdate",
)
# What defines the charge. Fixed once created: billing needs the historical price.
PRICE_IMMUTABLE = ("price", "priceType", "recurringChargePeriodType", "recurringChargePeriodLength")


def _href(resource: str, row_id: UUID) -> str:
    return f"{BASE}/{resource}/{row_id}"


def _created(body: dict[str, Any]) -> TmfJSONResponse:
    return TmfJSONResponse(body, status_code=201, headers={"Location": body["href"]})


# --- productSpecification ---


class SpecificationCreate(TmfModel):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    description: str | None = None
    # The spec's _FVO schema makes the client send lastUpdate; it is server-managed here.
    last_update: Any = None


class SpecificationPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    description: str | None = None


def specification_body(row: Row) -> dict[str, Any]:
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productSpecification", row["id"]),
            "name": row["name"],
            "description": row["description"],
            "lifecycleStatus": row["lifecycle_status"],
            "lastUpdate": utc(row["updated_at"]),
            "@type": "ProductSpecification",
        }
    )


@router.get("/productSpecification")
def list_specifications(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "lifecycleStatus": ("lifecycle_status", listing.as_str),
        },
        attributes=SPEC_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = catalog.list_product_specifications(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([specification_body(row) for row in rows], total, query)


@router.get("/productSpecification/{spec_id}")
def get_specification(spec_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, SPEC_ATTRIBUTES)
    with tx() as conn:
        row = catalog.get_product_specification(conn, parse_id("productSpecification", spec_id))
    if row is None:
        raise not_found("productSpecification", spec_id)
    return TmfJSONResponse(listing.select_fields(specification_body(row), fields))


@router.post("/productSpecification")
def create_specification(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(SpecificationCreate, body)
    with tx() as conn:
        row = catalog.create_product_specification(
            conn,
            name=data.name,
            lifecycle_status=data.lifecycle_status,
            description=data.description,
        )
    return _created(specification_body(row))


@router.patch("/productSpecification/{spec_id}")
def patch_specification(spec_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "ProductSpecification")
    data = parse(SpecificationPatch, body, read_only=("lastUpdate",))
    not_null(data, "name", "lifecycle_status")
    changes = data.model_dump(include=data.model_fields_set - {"at_type"})
    with tx() as conn:
        uid = parse_id("productSpecification", spec_id)
        if catalog.get_product_specification(conn, uid) is None:
            raise not_found("productSpecification", spec_id)
        row = catalog.update_product_specification(conn, uid, _columns(changes))
    return TmfJSONResponse(specification_body(row))


def _columns(changes: dict[str, Any]) -> dict[str, Any]:
    """Drop the TMF meta attributes: they are accepted but are not columns."""
    return {k: v for k, v in changes.items() if not k.startswith("at_")}


# --- productOfferingPrice ---


class PriceCreate(TmfModel):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    price_type: Literal["recurring", "oneTime"]
    price: Money
    valid_for: TimePeriod
    recurring_charge_period_type: Literal["monthly"] | None = None
    recurring_charge_period_length: Literal[1] = 1
    last_update: Any = None

    @model_validator(mode="after")
    def _recurring_has_period(self) -> "PriceCreate":
        if (self.price_type == "recurring") != (self.recurring_charge_period_type is not None):
            raise ValueError(
                "recurringChargePeriodType is required for a recurring price and not allowed"
                " for a oneTime price"
            )
        if self.price.value < 0:
            raise ValueError("price.value must not be negative")
        return self


class PricePatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None


def price_body(row: Row) -> dict[str, Any]:
    recurring = row["price_type"] == "recurring"
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productOfferingPrice", row["id"]),
            "name": row["name"],
            "lifecycleStatus": row["lifecycle_status"],
            "priceType": row["price_type"],
            "recurringChargePeriodType": row["recurring_period"],
            "recurringChargePeriodLength": row["recurring_period_length"] if recurring else None,
            "price": money(row["amount"], row["currency"]),
            "validFor": time_period(row["valid_for"]),
            "lastUpdate": utc(row["updated_at"]),
            "@type": "ProductOfferingPrice",
        }
    )


@router.get("/productOfferingPrice")
def list_prices(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "priceType": ("price_type", listing.as_str),
            "lifecycleStatus": ("lifecycle_status", listing.as_str),
        },
        attributes=PRICE_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = catalog.list_product_offering_prices(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([price_body(row) for row in rows], total, query)


@router.get("/productOfferingPrice/{price_id}")
def get_price(price_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PRICE_ATTRIBUTES)
    with tx() as conn:
        row = catalog.get_product_offering_price(conn, parse_id("productOfferingPrice", price_id))
    if row is None:
        raise not_found("productOfferingPrice", price_id)
    return TmfJSONResponse(listing.select_fields(price_body(row), fields))


@router.post("/productOfferingPrice")
def create_price(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(PriceCreate, body)
    with tx() as conn:
        row = catalog.create_product_offering_price(
            conn,
            product_offering_id=None,
            name=data.name,
            lifecycle_status=data.lifecycle_status,
            price_type=data.price_type,
            recurring_period=data.recurring_charge_period_type,
            amount=data.price.value,
            currency=data.price.unit,
            valid_for=data.valid_for.to_range(),
        )
    return _created(price_body(row))


@router.patch("/productOfferingPrice/{price_id}")
def patch_price(price_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "ProductOfferingPrice")
    data = parse(PricePatch, body, read_only=("lastUpdate", *PRICE_IMMUTABLE))
    not_null(data, "name", "lifecycle_status", "valid_for")
    changes = _columns(data.model_dump(include=data.model_fields_set))
    with tx() as conn:
        uid = parse_id("productOfferingPrice", price_id)
        current = catalog.get_product_offering_price(conn, uid)
        if current is None:
            raise not_found("productOfferingPrice", price_id)
        if data.valid_for is not None:
            new_range = data.valid_for.to_range()
            if new_range.lower != current["valid_for"].lower:
                # moving the start would rewrite which price applied in the past
                raise conflict("validFor.startDateTime cannot be changed; only the end can")
            changes["valid_for"] = new_range
        try:
            row = catalog.update_product_offering_price(conn, uid, changes)
        except pg_errors.ExclusionViolation:
            raise conflict(
                "validFor overlaps another price of the same type and currency on the offering"
            ) from None
    return TmfJSONResponse(price_body(row))


# --- productOffering ---


class OfferingCreate(TmfModel):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    valid_for: TimePeriod
    product_specification: Ref
    description: str | None = None
    is_sellable: bool = True
    product_offering_price: list[Ref] = []
    last_update: Any = None


class OfferingPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None
    description: str | None = None
    is_sellable: bool | None = None
    product_offering_price: list[Ref] | None = None


def offering_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    spec = catalog.get_product_specification(conn, row["product_specification_id"])
    assert spec is not None  # foreign key
    prices = catalog.prices_of_offering(conn, row["id"])
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productOffering", row["id"]),
            "name": row["name"],
            "description": row["description"],
            "lifecycleStatus": row["lifecycle_status"],
            "isSellable": row["is_sellable"],
            "validFor": time_period(row["valid_for"]),
            "productSpecification": ref(
                "ProductSpecification",
                _href("productSpecification", spec["id"]),
                spec["id"],
                spec["name"],
            ),
            "productOfferingPrice": [
                ref(
                    "ProductOfferingPrice",
                    _href("productOfferingPrice", price["id"]),
                    price["id"],
                    price["name"],
                )
                for price in prices
            ],
            "lastUpdate": utc(row["updated_at"]),
            "@type": "ProductOffering",
        }
    )


def _attach_prices(conn: psycopg.Connection, offering_id: UUID, refs: list[Ref]) -> None:
    for price_ref in refs:
        try:
            attached = catalog.attach_price(conn, price_ref.id, offering_id)
        except pg_errors.ExclusionViolation:
            raise conflict(
                f"productOfferingPrice {price_ref.id} overlaps a price of the same type and"
                " currency already on this offering"
            ) from None
        if attached is not None:
            continue
        price = catalog.get_product_offering_price(conn, price_ref.id)
        if price is None:
            raise unprocessable(f"productOfferingPrice {price_ref.id} does not exist")
        if price["product_offering_id"] != offering_id:
            raise conflict(
                f"productOfferingPrice {price_ref.id} already belongs to another offering"
            )


@router.get("/productOffering")
def list_offerings(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "lifecycleStatus": ("lifecycle_status", listing.as_str),
            "isSellable": ("is_sellable", listing.as_bool),
            "productSpecification.id": ("product_specification_id", listing.as_uuid),
        },
        attributes=OFFERING_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = catalog.list_product_offerings(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [offering_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/productOffering/{offering_id}")
def get_offering(offering_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, OFFERING_ATTRIBUTES)
    with tx() as conn:
        row = catalog.get_product_offering(conn, parse_id("productOffering", offering_id))
        if row is None:
            raise not_found("productOffering", offering_id)
        body = offering_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post("/productOffering")
def create_offering(body: JsonBody, tx: Tx) -> TmfJSONResponse:
    data = parse(OfferingCreate, body)
    valid_for = data.valid_for.to_range()
    with tx() as conn:
        if catalog.get_product_specification(conn, data.product_specification.id) is None:
            raise unprocessable(
                f"productSpecification {data.product_specification.id} does not exist"
            )
        row = catalog.create_product_offering(
            conn,
            product_specification_id=data.product_specification.id,
            name=data.name,
            lifecycle_status=data.lifecycle_status,
            valid_for=valid_for,
            is_sellable=data.is_sellable,
            description=data.description,
        )
        _attach_prices(conn, row["id"], data.product_offering_price)
        created = offering_body(conn, row)
    return _created(created)


@router.patch("/productOffering/{offering_id}")
def patch_offering(offering_id: str, body: MergePatchBody, tx: Tx) -> TmfJSONResponse:
    check_type(body, "ProductOffering")
    data = parse(OfferingPatch, body, read_only=("lastUpdate", "productSpecification"))
    not_null(data, "name", "lifecycle_status", "valid_for", "is_sellable", "product_offering_price")
    changes = _columns(
        data.model_dump(include=data.model_fields_set - {"valid_for", "product_offering_price"})
    )
    if data.valid_for is not None:
        changes["valid_for"] = data.valid_for.to_range()
    with tx() as conn:
        uid = parse_id("productOffering", offering_id)
        if catalog.get_product_offering(conn, uid) is None:
            raise not_found("productOffering", offering_id)
        if data.product_offering_price is not None:
            # merge patch replaces the whole array, so a missing entry would be a detach
            wanted = {price_ref.id for price_ref in data.product_offering_price}
            dropped = [p for p in catalog.prices_of_offering(conn, uid) if p["id"] not in wanted]
            if dropped:
                raise conflict(
                    "a price cannot be detached from an offering (end its validFor instead):"
                    f" {', '.join(str(p['id']) for p in dropped)}"
                )
            _attach_prices(conn, uid, data.product_offering_price)
        row = catalog.update_product_offering(conn, uid, changes)
        patched = offering_body(conn, row)
    return TmfJSONResponse(patched)
