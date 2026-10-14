"""TMF620 Product Catalog Management v5: productSpecification, productOffering,
productOfferingPrice.

A price is created on its own and attached by the offering (productOfferingPrice[]), as
in the spec. A price belongs to at most one offering and is never detached or re-priced:
billing needs the historical price, so changing a price means ending its validFor - not
retroactively - and attaching a new one. Nor does a price ever start in the past: one
created or attached with validFor.startDateTime < now is a 409.

An offering may carry a code, as the spec's externalIdentifier with exactly one entry
{id: <code>, externalIdentifierType: "offeringCode", owner: "bss"}. The code is unique
(409), upper case (^[A-Z0-9][A-Z0-9_-]{0,31}$), set on POST only and read-only after.
"""

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from psycopg import errors as pg_errors
from pydantic import Field, model_validator

from app.api import listing
from app.api.common import (
    Money,
    ProductOfferingPriceRef,
    ProductSpecificationRef,
    TimePeriod,
    TmfModel,
    billing_tz,
    check_type,
    columns,
    created,
    money,
    not_null,
    parse,
    parse_id,
    ref,
    time_period,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.repository import catalog
from app.repository._sql import Row

BASE = "/tmf-api/productCatalogManagement/v5"
router = APIRouter(prefix=BASE)

LifecycleStatus = Literal["In Design", "Active", "Retired"]

# Every catalog entity's lifecycle only moves forward: In Design -> Active -> Retired, and
# Retired is final. Repeating the current status is not a transition (a merge patch may
# repeat values). Ordering refuses an offering that is not Active, so reopening a Retired
# one would silently make it sellable again; going back to In Design would hide a live
# one. Specifications and prices follow the same steps, so the catalog has one lifecycle.
LIFECYCLE_STEPS = {("In Design", "Active"), ("Active", "Retired")}


def _check_lifecycle(entity: str, current: str, wanted: str) -> None:
    if wanted == current or (current, wanted) in LIFECYCLE_STEPS:
        return
    if current == "Retired":
        raise conflict(f"lifecycleStatus: Retired is final; the {entity} cannot change status")
    raise conflict(
        f"lifecycleStatus cannot go from {current} to {wanted}: only In Design -> Active -> Retired"
    )


SPEC_ATTRIBUTES = ("name", "description", "lifecycleStatus", "lastUpdate")
OFFERING_ATTRIBUTES = (
    "name",
    "description",
    "lifecycleStatus",
    "isSellable",
    "validFor",
    "productSpecification",
    "productOfferingPrice",
    "externalIdentifier",
    "lastUpdate",
)
CODE_TYPE = "offeringCode"
CODE_OWNER = "bss"
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
def create_specification(body: JsonBody, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, SPEC_ATTRIBUTES)
    check_type(body, "ProductSpecification")
    data = parse(SpecificationCreate, body)
    with tx() as conn:
        row = catalog.create_product_specification(
            conn,
            name=data.name,
            lifecycle_status=data.lifecycle_status,
            description=data.description,
        )
    return created(listing.select_fields(specification_body(row), fields))


@router.patch("/productSpecification/{spec_id}")
def patch_specification(
    spec_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, SPEC_ATTRIBUTES)
    check_type(body, "ProductSpecification")
    data = parse(SpecificationPatch, body, read_only=("lastUpdate",))
    not_null(data, "name", "lifecycle_status")
    changes = columns(data)
    with tx() as conn:
        uid = parse_id("productSpecification", spec_id)
        current = catalog.get_product_specification(conn, uid)
        if current is None:
            raise not_found("productSpecification", spec_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("specification", current["lifecycle_status"], data.lifecycle_status)
        row = catalog.update_product_specification(conn, uid, changes)
    return TmfJSONResponse(listing.select_fields(specification_body(row), fields))


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
def create_price(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PRICE_ATTRIBUTES)
    check_type(body, "ProductOfferingPrice")
    data = parse(PriceCreate, body)
    valid_for = data.valid_for.to_range()
    if valid_for.lower < clock.now():
        raise conflict(
            "validFor.startDateTime is in the past; a price can only take effect from now on"
        )
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
            valid_for=valid_for,
        )
    return created(listing.select_fields(price_body(row), fields))


def _first_uncovered_day(end: datetime) -> date:
    """A price valid until `end` (exclusive) prices every midnight before it: the first
    business date whose midnight it no longer covers."""
    local = end.astimezone(billing_tz())
    return local.date() if local.time() == time.min else local.date() + timedelta(days=1)


def _no_gap(conn: psycopg.Connection, price: Row, new_end: datetime) -> None:
    """Ending (or moving the end of) a recurring price must not leave a product of its
    offering without a price: billing prices every period at its first billable day's
    midnight, and a price can never be created in the past to fill a gap later
    (stage 4 design, D-W18). Allowed if a successor of the same type and currency starts
    exactly at the new end, or if every such product is terminated by then."""
    offering_id = price["product_offering_id"]
    if offering_id is None or price["price_type"] != "recurring":
        return  # an unattached price prices nothing; one-time prices are not billed
    if catalog.successor_price(
        conn,
        offering_id=offering_id,
        price_type=price["price_type"],
        currency=price["currency"],
        starts_at=new_end,
        exclude_id=price["id"],
    ):
        return
    day = _first_uncovered_day(new_end)
    uncovered = catalog.products_billable_from(
        conn, offering_id=offering_id, currency=price["currency"], day=day
    )
    if uncovered:
        raise conflict(
            f"validFor.endDateTime would leave {len(uncovered)} product(s) of this offering"
            f" in {price['currency']} without a price from {day}: terminate them by then,"
            " or attach a price that starts exactly at the new end"
        )


@router.patch("/productOfferingPrice/{price_id}")
def patch_price(
    price_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PRICE_ATTRIBUTES)
    check_type(body, "ProductOfferingPrice")
    data = parse(PricePatch, body, read_only=("lastUpdate", *PRICE_IMMUTABLE))
    not_null(data, "name", "lifecycle_status", "valid_for")
    changes = columns(data)
    with tx() as conn:
        uid = parse_id("productOfferingPrice", price_id)
        current = catalog.get_product_offering_price(conn, uid)
        if current is None:
            raise not_found("productOfferingPrice", price_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("price", current["lifecycle_status"], data.lifecycle_status)
        if data.valid_for is not None:
            new_range = data.valid_for.to_range()
            if new_range.lower != current["valid_for"].lower:
                # moving the start would rewrite which price applied in the past
                raise conflict("validFor.startDateTime cannot be changed; only the end can")
            old_end, new_end, now = current["valid_for"].upper, new_range.upper, clock.now()
            if new_end != old_end and any(
                end is not None and end < now for end in (old_end, new_end)
            ):
                # a retroactive end (or reopening a price that already ended) would change
                # which price applied to periods that may already be billed
                raise conflict(
                    "validFor.endDateTime can only be set or moved to the present or the"
                    " future, and only while the price has not ended yet"
                )
            if new_end is not None and new_end != old_end:
                _no_gap(conn, current, new_end)
            changes["valid_for"] = new_range
        try:
            row = catalog.update_product_offering_price(conn, uid, changes)
        except pg_errors.ExclusionViolation:
            raise conflict(
                "validFor overlaps another price of the same type and currency on the offering"
            ) from None
    return TmfJSONResponse(listing.select_fields(price_body(row), fields))


# --- productOffering ---


class OfferingCode(TmfModel):
    """One externalIdentifier entry: the offering code."""

    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
    external_identifier_type: Literal["offeringCode"]
    owner: Literal["bss"] = CODE_OWNER


class OfferingCreate(TmfModel):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    valid_for: TimePeriod
    product_specification: ProductSpecificationRef
    description: str | None = None
    is_sellable: bool = True
    product_offering_price: list[ProductOfferingPriceRef] = []
    external_identifier: list[OfferingCode] = Field(default=[], max_length=1)
    last_update: Any = None


class OfferingPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None
    description: str | None = None
    is_sellable: bool | None = None
    product_offering_price: list[ProductOfferingPriceRef] | None = None


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
            "externalIdentifier": None
            if row["code"] is None
            else [
                {
                    "id": row["code"],
                    "externalIdentifierType": CODE_TYPE,
                    "owner": CODE_OWNER,
                    "@type": "ExternalIdentifier",
                }
            ],
            "lastUpdate": utc(row["updated_at"]),
            "@type": "ProductOffering",
        }
    )


def _check_offering_period(old: Any, new: Any, now: datetime) -> None:
    """An offering's validFor decides which start dates new orders may have
    (offeringNotValid); changing it in the past would rewrite what was orderable then.
    The start may not move into the past and the end may not be set or moved into the
    past; repeating the current value is not a change. Billing never reads it, so no
    change here can leave a product without a price (prices: D-W18, D-W20)."""
    if new.lower != old.lower and new.lower < now:
        raise conflict(
            "validFor.startDateTime cannot be moved into the past; only to the present or"
            " the future"
        )
    if new.upper != old.upper and new.upper is not None and new.upper < now:
        raise conflict(
            "validFor.endDateTime cannot be set into the past; only to the present or the future"
        )


def _not_in_the_past(price_id: UUID, start: datetime, now: datetime) -> None:
    # Stage 3 catch-up billing charges every day since a product's start at the price
    # valid then: a price that starts in the past would add back-charges for days that
    # were already billed (or were free).
    if start < now:
        raise conflict(
            f"productOfferingPrice {price_id}: validFor.startDateTime is in the past;"
            " a price can only take effect from now on"
        )


def _attach_prices(
    conn: psycopg.Connection,
    offering_id: UUID,
    refs: list[ProductOfferingPriceRef],
    now: datetime,
) -> None:
    for price_ref in refs:
        price = catalog.get_product_offering_price(conn, price_ref.id)
        if price is None:
            raise unprocessable(f"productOfferingPrice {price_ref.id} does not exist")
        if price["product_offering_id"] == offering_id:
            continue  # already attached: a merge patch repeats the whole list
        if price["product_offering_id"] is not None:
            raise conflict(
                f"productOfferingPrice {price_ref.id} already belongs to another offering"
            )
        _not_in_the_past(price_ref.id, price["valid_for"].lower, now)
        try:
            attached = catalog.attach_price(conn, price_ref.id, offering_id)
        except pg_errors.ExclusionViolation:
            raise conflict(
                f"productOfferingPrice {price_ref.id} overlaps a price of the same type and"
                " currency already on this offering"
            ) from None
        if attached is None:  # attached elsewhere between our read and the update
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
            "externalIdentifier.id": ("code", listing.as_str),
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
def create_offering(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, OFFERING_ATTRIBUTES)
    check_type(body, "ProductOffering")
    data = parse(OfferingCreate, body)
    valid_for = data.valid_for.to_range()
    with tx() as conn:
        if catalog.get_product_specification(conn, data.product_specification.id) is None:
            raise unprocessable(
                f"productSpecification {data.product_specification.id} does not exist"
            )
        code = data.external_identifier[0].id if data.external_identifier else None
        try:
            with conn.transaction():
                row = catalog.create_product_offering(
                    conn,
                    product_specification_id=data.product_specification.id,
                    name=data.name,
                    lifecycle_status=data.lifecycle_status,
                    valid_for=valid_for,
                    is_sellable=data.is_sellable,
                    description=data.description,
                    code=code,
                )
        except pg_errors.UniqueViolation:
            raise conflict(f"offering code {code} is already in use") from None
        _attach_prices(conn, row["id"], data.product_offering_price, clock.now())
        new = offering_body(conn, row)
    return created(listing.select_fields(new, fields))


@router.patch("/productOffering/{offering_id}")
def patch_offering(
    offering_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, OFFERING_ATTRIBUTES)
    check_type(body, "ProductOffering")
    data = parse(
        OfferingPatch,
        body,
        read_only=("lastUpdate", "productSpecification", "externalIdentifier"),
    )
    not_null(data, "name", "lifecycle_status", "valid_for", "is_sellable", "product_offering_price")
    changes = columns(data, exclude=frozenset({"valid_for", "product_offering_price"}))
    if data.valid_for is not None:
        changes["valid_for"] = data.valid_for.to_range()
    with tx() as conn:
        uid = parse_id("productOffering", offering_id)
        current = catalog.get_product_offering(conn, uid)
        if current is None:
            raise not_found("productOffering", offering_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("offering", current["lifecycle_status"], data.lifecycle_status)
        if "valid_for" in changes:
            _check_offering_period(current["valid_for"], changes["valid_for"], clock.now())
        if data.product_offering_price is not None:
            # merge patch replaces the whole array, so a missing entry would be a detach
            wanted = {price_ref.id for price_ref in data.product_offering_price}
            dropped = [p for p in catalog.prices_of_offering(conn, uid) if p["id"] not in wanted]
            if dropped:
                raise conflict(
                    "a price cannot be detached from an offering (end its validFor instead):"
                    f" {', '.join(str(p['id']) for p in dropped)}"
                )
            _attach_prices(conn, uid, data.product_offering_price, clock.now())
        row = catalog.update_product_offering(conn, uid, changes)
        patched = offering_body(conn, row)
    return TmfJSONResponse(listing.select_fields(patched, fields))
