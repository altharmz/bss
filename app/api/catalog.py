"""TMF620 Product Catalog Management v5: productSpecification, productOffering,
productOfferingPrice.

A price is created on its own, or inline in an offering's productOfferingPrice[], and
attached by the offering, as in the spec. A price belongs to at most one offering and is
never detached; once its offering was ordered, its charge is fixed: billing needs the
historical price, so changing it means ending its validFor - not retroactively - and
attaching a new one. Nor does a price ever start in the past: one created or attached
with validFor.startDateTime < now is a 409.

An offering may carry a code: the externalIdentifier entry with externalIdentifierType
"offeringCode" (owner "bss"). The code is unique (409) and upper case
(^[A-Z0-9][A-Z0-9_-]{0,31}$).

The attributes with no behaviour here (brand, version, attachment, terms, a price's
popRelationship, ...) are documents: checked against the spec and stored as sent
(docs/decisions/catalog.md, "Catalog attributes"). A price's tax is one too, stored as
sent, but billing applies it: `tax_problems` holds it to billing's rules, and it is part
of the charge (docs/decisions/billing-core.md, "Taxes").

priceType is recurring, oneTime, or one of the alterations TMF620 names as examples
(discount, allowance, penalty): those have no arithmetic in the spec, so they are records
- stored, served, exported - and billing never applies them (docs/decisions/catalog.md,
"Alteration price types"). Billing charges recurring prices only.

DELETE removes only what nothing references (see "deletes" below and in
app/api/classification.py).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from psycopg import errors as pg_errors
from psycopg.types.range import Range
from pydantic import Field, ValidationError, model_validator

from app.api import listing
from app.api.common import (
    Money,
    ProductOfferingPriceRef,
    ProductOfferingRef,
    ProductSpecificationRef,
    Ref,
    TimePeriod,
    TmfModel,
    billing_tz,
    check_documented,
    check_type,
    claim_id,
    columns,
    created,
    documents_field,
    documents_model,
    merge_documents,
    meta_body,
    money,
    not_null,
    parse_documented,
    parse_id,
    ref,
    same,
    take_id,
    time_period,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import (
    Problem,
    TmfError,
    bad_request,
    conflict,
    not_found,
    problems_error,
    unprocessable,
    validation_problems,
)
from app.api.tmfjson import TmfJSONResponse
from app.billing.core import tax_of
from app.events import emit
from app.repository import catalog, classification
from app.repository._sql import Row, count_references

API = "TMF620"
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


# Documents to us: stored as sent (checked against the spec), no behaviour. References
# among them (bundledProductSpecification, productOfferingRelationship, popRelationship,
# a specification's category, ...) are not resolved, as the spec's own examples need.
SPEC_DOCUMENTS = (
    "attachment",
    "brand",
    "bundledProductSpecification",
    "category",
    "externalIdentifier",
    "intentSpecification",
    "isBundle",
    "policy",
    "productNumber",
    "productSpecCharacteristic",
    "productSpecificationRelationship",
    "relatedParty",
    "resourceSpecification",
    "serviceSpecification",
    "targetProductSchema",
    "validFor",
    "version",
)
SPEC_ATTRIBUTES = ("name", "description", "lifecycleStatus", "lastUpdate", *SPEC_DOCUMENTS)
# externalIdentifier is stored as sent too; its offeringCode entry is also the code column.
OFFERING_DOCUMENTS = (
    "agreement",
    "allowedAction",
    "attachment",
    "bundledGroupProductOffering",
    "bundledProductOffering",
    "channel",
    "externalIdentifier",
    "isBundle",
    "marketSegment",
    "place",
    "policy",
    "prodSpecCharValueUse",
    "productOfferingCharacteristic",
    "productOfferingRelationship",
    "productOfferingTerm",
    "resourceCandidate",
    "serviceCandidate",
    "serviceLevelAgreement",
    "statusReason",
    "version",
)
OFFERING_ATTRIBUTES = (
    "name",
    "description",
    "lifecycleStatus",
    "isSellable",
    "validFor",
    "productSpecification",
    "productOfferingPrice",
    "lastUpdate",
    "category",
    *OFFERING_DOCUMENTS,
)
CODE_TYPE = "offeringCode"
CODE_OWNER = "bss"
# A price's percentage and popRelationship are records: billing charges `price`, and
# adds `tax` on top of it (tax_problems).
PRICE_DOCUMENTS = (
    "bundledPopRelationship",
    "description",
    "externalIdentifier",
    "isBundle",
    "percentage",
    "place",
    "policy",
    "popRelationship",
    "pricingLogicAlgorithm",
    "prodSpecCharValueUse",
    "productOfferingTerm",
    "tax",
    "unitOfMeasure",
    "version",
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
    *PRICE_DOCUMENTS,
)
# What defines the charge: changeable only while the price has priced nothing.
PRICE_CHARGE = (
    "price",
    "price_type",
    "recurring_charge_period_type",
    "recurring_charge_period_length",
)


def _href(resource: str, row_id: UUID) -> str:
    return f"{BASE}/{resource}/{row_id}"


def _document_fields(documents: tuple[str, ...]) -> frozenset[str]:
    return frozenset(map(documents_field, documents))


def _read_only(body: dict[str, Any], *names: str) -> None:
    for name in names:
        if name in body:
            raise bad_request(f"{name} is read-only")


def _prefixed(problems: list[Problem], path: str) -> list[Problem]:
    return [(f"{path}.{loc}" if loc else path, msg, unknown) for loc, msg, unknown in problems]


# --- references: a `version` (and a specification's targetProductSchema) must be the
# referred entry's current one - no versions are kept, so no other can be referred to ---


class SpecificationRef(ProductSpecificationRef):
    version: str | None = None
    target_product_schema: dict[str, Any] | None = None


class CategoryRef(Ref):
    referred_type = "Category"
    version: str | None = None


class OfferingRef(ProductOfferingRef):
    version: str | None = None


class PriceRef(ProductOfferingPriceRef):
    version: str | None = None


def version_of(row: Row) -> str | None:
    """A category's or catalog's version is a column; the others' a document."""
    return row["version"] if "version" in row else row["details"].get("version")


def check_version(reference: Ref, row: Row, name: str) -> None:
    sent = getattr(reference, "version", None)
    current = version_of(row)
    if sent is not None and sent != current:
        held = f"its current version is {current}" if current else "it has no version"
        raise unprocessable(
            f"{name}.version: {reference.id} is referred to at version {sent}, but {held}"
        )


def offering_ref(offering: Row) -> dict[str, Any]:
    return versioned(
        ref("ProductOffering", _href("productOffering", offering["id"]), offering["id"],
            offering["name"]),
        offering,
    )  # fmt: skip


def versioned(body: dict[str, Any], row: Row) -> dict[str, Any]:
    """A reference body with the referred entry's version, when it has one."""
    version = version_of(row)
    return body | {"version": version} if version else body


def specification_ref(spec: Row) -> dict[str, Any]:
    body = versioned(
        ref("ProductSpecification", _href("productSpecification", spec["id"]), spec["id"],
            spec["name"]),
        spec,
    )  # fmt: skip
    schema = spec["details"].get("targetProductSchema")
    return body | {"targetProductSchema": schema} if schema else body


# --- productSpecification ---

SpecificationDocuments = documents_model("SpecificationDocuments", API, SPEC_DOCUMENTS)


class SpecificationCreate(SpecificationDocuments):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    description: str | None = None
    # The spec's _FVO schema makes the client send lastUpdate; it is server-managed here.
    last_update: Any = None


class SpecificationPatch(SpecificationDocuments):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    description: str | None = None


def specification_body(row: Row) -> dict[str, Any]:
    body = without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productSpecification", row["id"]),
            "name": row["name"],
            "description": row["description"],
            "lifecycleStatus": row["lifecycle_status"],
            "lastUpdate": utc(row["updated_at"]),
        }
    )
    return {**body, **row["details"], "@type": "ProductSpecification"} | meta_body(row)


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
def create_specification(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, SPEC_ATTRIBUTES)
    check_type(body, "ProductSpecification")
    uid = take_id(body)
    data, documents = parse_documented(
        SpecificationCreate, body, SPEC_DOCUMENTS, API, "ProductSpecification_FVO"
    )
    with tx() as conn:
        claim_id(conn, "product_specification", uid)
        new = specification_body(insert_specification(conn, data, documents, uid))
        emit.created(conn, API, "productSpecification", new, at=clock.now())
    return created(listing.select_fields(new, fields))


def insert_specification(
    conn: psycopg.Connection,
    data: SpecificationCreate,
    documents: dict[str, Any],
    new_id: UUID | None = None,
) -> Row:
    """POST's write; an import passes the source's id."""
    row = catalog.create_product_specification(
        conn,
        name=data.name,
        lifecycle_status=data.lifecycle_status,
        description=data.description,
        details=merge_documents({}, documents),
        id=new_id,
    )
    return with_meta(conn, "product_specification", row, data)


@router.patch("/productSpecification/{spec_id}")
def patch_specification(
    spec_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, SPEC_ATTRIBUTES)
    check_type(body, "ProductSpecification")
    _read_only(body, "lastUpdate")
    data, documents = parse_documented(
        SpecificationPatch, body, SPEC_DOCUMENTS, API, "ProductSpecification_MVO"
    )
    not_null(data, "name", "lifecycle_status")
    changes = columns(data, exclude=_document_fields(SPEC_DOCUMENTS))
    with tx() as conn:
        uid = parse_id("productSpecification", spec_id)
        current = catalog.get_product_specification(conn, uid)
        if current is None:
            raise not_found("productSpecification", spec_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("specification", current["lifecycle_status"], data.lifecycle_status)
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        old = emit.before(conn, API, "productSpecification", lambda: specification_body(current))
        row = catalog.update_product_specification(conn, uid, changes)
        new = specification_body(with_meta(conn, "product_specification", row, data))
        emit.changed(
            conn, API, "productSpecification", old, new, at=clock.now(), state="lifecycleStatus"
        )
    return TmfJSONResponse(listing.select_fields(new, fields))


# --- productOfferingPrice ---

PriceDocuments = documents_model("PriceDocuments", API, PRICE_DOCUMENTS)


PriceType = Literal["recurring", "oneTime", "discount", "allowance", "penalty"]


def _check_charge(price_type: str, period: str | None, price: Money | None) -> None:
    if (price_type == "recurring") != (period is not None):
        raise ValueError(
            "recurringChargePeriodType is required for a recurring price and not allowed"
            f" for a {price_type} price"
        )
    if price is not None and price.value < 0:
        raise ValueError("price.value must not be negative")


def tax_problems(tax: list[dict[str, Any]] | None, price: Money | None) -> list[Problem]:
    """A price's tax items, as billing applies them (docs/decisions/billing-core.md#taxes-
    2026-10-18): each names a taxCategory (once) and a taxRate in percent, 0..100; a
    taxAmount, if sent, is the tax of a full month, price x rate / 100, in its currency.
    Shape and types are the spec's (checked before)."""
    problems: list[Problem] = []
    seen: set[str] = set()
    for n, item in enumerate(tax or ()):
        path = f"tax.{n}"
        category, rate = item.get("taxCategory"), item.get("taxRate")
        if not category:
            problems.append((f"{path}.taxCategory", "required (billing groups taxes by it)", False))
        elif category in seen:
            problems.append((f"{path}.taxCategory", f"{category} twice on one price", False))
        else:
            seen.add(category)
        if rate is None:
            problems.append((f"{path}.taxRate", "required (a rate in percent)", False))
            continue
        if not 0 <= rate <= 100:
            problems.append((f"{path}.taxRate", "must be a percent from 0 to 100", False))
            continue
        sent = item.get("taxAmount")
        if sent is None:
            continue
        if price is None:
            problems.append((f"{path}.taxAmount", "needs the price it is the tax of", False))
        elif sent.get("unit") != price.unit or sent.get("value") != tax_of(price.value, rate):
            problems.append(
                (
                    f"{path}.taxAmount",
                    f"must be {tax_of(price.value, rate)} {price.unit} (price x taxRate / 100)",
                    False,
                )
            )
    return problems


class PriceCreate(PriceDocuments):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    price_type: PriceType
    # Optional, as in the spec; a price without one cannot be attached to an offering.
    price: Money | None = None
    # Absent: from now on.
    valid_for: TimePeriod | None = None
    recurring_charge_period_type: Literal["daily", "weekly", "monthly", "yearly"] | None = None
    recurring_charge_period_length: int = Field(default=1, ge=1, le=1000, strict=True)
    last_update: Any = None

    @model_validator(mode="after")
    def _recurring_has_period(self) -> "PriceCreate":
        _check_charge(self.price_type, self.recurring_charge_period_type, self.price)
        return self


class PricePatch(PriceDocuments):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None
    # The charge: only while the price has priced nothing (see patch_price).
    price: Money | None = None
    price_type: PriceType | None = None
    recurring_charge_period_type: Literal["daily", "weekly", "monthly", "yearly"] | None = None
    recurring_charge_period_length: int | None = Field(default=None, ge=1, le=1000, strict=True)


def price_body(row: Row) -> dict[str, Any]:
    recurring = row["price_type"] == "recurring"
    body = without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productOfferingPrice", row["id"]),
            "name": row["name"],
            "lifecycleStatus": row["lifecycle_status"],
            "priceType": row["price_type"],
            "recurringChargePeriodType": row["recurring_period"],
            "recurringChargePeriodLength": row["recurring_period_length"] if recurring else None,
            "price": None if row["amount"] is None else money(row["amount"], row["currency"]),
            "validFor": time_period(row["valid_for"]),
            "lastUpdate": utc(row["updated_at"]),
        }
    )
    return {**body, **row["details"], "@type": "ProductOfferingPrice"} | meta_body(row)


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
    uid = take_id(body)
    data, documents = parse_documented(
        PriceCreate, body, PRICE_DOCUMENTS, API, "ProductOfferingPrice_FVO"
    )
    with tx() as conn:
        claim_id(conn, "product_offering_price", uid)
        new = price_body(insert_price(conn, data, documents, clock.now(), uid))
        emit.created(conn, API, "productOfferingPrice", new, at=clock.now())
    return created(listing.select_fields(new, fields))


def insert_price(
    conn: psycopg.Connection,
    data: PriceCreate,
    documents: dict[str, Any],
    now: datetime,
    new_id: UUID | None = None,
) -> Row:
    """POST's rule and write; an import passes the source's id."""
    problems = tax_problems(documents.get("tax"), data.price)
    if problems:
        raise problems_error(problems)
    valid_for = Range(now, None, "[)") if data.valid_for is None else data.valid_for.to_range()
    if valid_for.lower < now:
        raise conflict(
            "validFor.startDateTime is in the past; a price can only take effect from now on"
        )
    row = catalog.create_product_offering_price(
        conn,
        product_offering_id=None,
        name=data.name,
        lifecycle_status=data.lifecycle_status,
        price_type=data.price_type,
        recurring_period=data.recurring_charge_period_type,
        recurring_period_length=data.recurring_charge_period_length,
        amount=None if data.price is None else data.price.value,
        currency=None if data.price is None else data.price.unit,
        valid_for=valid_for,
        details=merge_documents({}, documents),
        id=new_id,
    )
    return with_meta(conn, "product_offering_price", row, data)


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


def _stored_money(row: Row) -> Money | None:
    return None if row["amount"] is None else Money(unit=row["currency"], value=row["amount"])


def _charge_changes(
    conn: psycopg.Connection, current: Row, data: PricePatch, documents: dict[str, Any]
) -> dict[str, Any]:
    """The columns a PATCH of the charge (price, priceType, recurring period, tax) sets.
    Only while the price has priced nothing - unattached, or its offering never ordered -
    the same condition as DELETE: billing needs the historical charge (409 otherwise). The
    tax itself is a document (written by the caller); here it only counts as a change."""
    sent = set(PRICE_CHARGE) & data.model_fields_set
    tax_changed = "tax" in documents and documents["tax"] != current["details"].get("tax")
    if "price" in sent or "tax" in documents:
        price = data.price if "price" in sent else _stored_money(current)
        tax = documents["tax"] if "tax" in documents else current["details"].get("tax")
        problems = tax_problems(tax, price)
        if problems:
            raise problems_error(problems)
    if not sent and not tax_changed:
        return {}
    not_null(data, "price_type")
    price_type = data.price_type if "price_type" in sent else current["price_type"]
    period = (
        data.recurring_charge_period_type
        if "recurring_charge_period_type" in sent
        else current["recurring_period"]
    )
    if "recurring_charge_period_length" in sent and price_type != "recurring":
        raise unprocessable("recurringChargePeriodLength is only for a recurring price")
    changes: dict[str, Any] = {"price_type": price_type, "recurring_period": period}
    if "recurring_charge_period_length" in sent:
        not_null(data, "recurring_charge_period_length")
        changes["recurring_period_length"] = data.recurring_charge_period_length
    if "price" in sent:
        price = data.price
        changes["amount"] = None if price is None else price.value
        changes["currency"] = None if price is None else price.unit
    try:
        _check_charge(price_type, period, data.price)
    except ValueError as exc:
        raise unprocessable(str(exc)) from None
    if not tax_changed and all(current[column] == value for column, value in changes.items()):
        return {}  # the current charge repeated (a merge patch may repeat values)
    offering_id = current["product_offering_id"]
    if offering_id is not None:
        catalog.lock_offering(conn, offering_id)  # orders hold it FOR SHARE
        if _ever_ordered(conn, offering_id):
            raise conflict(
                "the charge of a price whose offering was ordered cannot change (billing"
                " needs it): end its validFor and attach a new price"
            )
        if changes.get("amount", current["amount"]) is None:
            raise conflict("price cannot be removed from a price attached to an offering")
        _check_cadence(conn, offering_id, current | changes)
    return changes


def _check_cadence(conn: psycopg.Connection, offering_id: UUID, price: Row) -> None:
    if price["price_type"] != "recurring":
        return
    for other in catalog.prices_of_offering(conn, offering_id):
        if (
            other["id"] != price["id"]
            and other["price_type"] == "recurring"
            and other["currency"] == price["currency"]
            and (other["recurring_period"], other["recurring_period_length"])
            != (price["recurring_period"], price["recurring_period_length"])
        ):
            raise conflict(
                "successor prices must keep the offering's charge interval; "
                "use another offering to change the subscription interval"
            )


@router.patch("/productOfferingPrice/{price_id}")
def patch_price(
    price_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PRICE_ATTRIBUTES)
    check_type(body, "ProductOfferingPrice")
    _read_only(body, "lastUpdate")
    data, documents = parse_documented(
        PricePatch, body, PRICE_DOCUMENTS, API, "ProductOfferingPrice_MVO"
    )
    not_null(data, "name", "lifecycle_status", "valid_for")
    changes = columns(data, exclude=_document_fields(PRICE_DOCUMENTS) | set(PRICE_CHARGE))
    with tx() as conn:
        uid = parse_id("productOfferingPrice", price_id)
        current = catalog.get_product_offering_price(conn, uid)
        if current is None:
            raise not_found("productOfferingPrice", price_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("price", current["lifecycle_status"], data.lifecycle_status)
        old = emit.before(conn, API, "productOfferingPrice", lambda: price_body(current))
        changes |= _charge_changes(conn, current, data, documents)
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
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
            with conn.transaction():
                row = catalog.update_product_offering_price(conn, uid, changes)
        except pg_errors.ExclusionViolation:
            raise conflict(
                "validFor overlaps another price of the same type and currency on the offering"
            ) from None
        new = price_body(with_meta(conn, "product_offering_price", row, data))
        emit.changed(
            conn, API, "productOfferingPrice", old, new, at=clock.now(), state="lifecycleStatus"
        )
    return TmfJSONResponse(listing.select_fields(new, fields))


# --- productOffering ---


class OfferingCode(TmfModel):
    """The externalIdentifier entry that is the offering code."""

    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
    external_identifier_type: Literal["offeringCode"]
    owner: Literal["bss"] = CODE_OWNER


OfferingDocuments = documents_model("OfferingDocuments", API, OFFERING_DOCUMENTS)


class OfferingCreate(OfferingDocuments):
    name: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus
    # Absent: orderable at any time.
    valid_for: TimePeriod | None = None
    product_specification: SpecificationRef | None = None
    description: str | None = None
    is_sellable: bool = True
    # ProductOfferingPriceRefOrValue: references, or new prices (checked by `price_entries`)
    product_offering_price: list[Any] = []
    category: list[CategoryRef] = []
    last_update: Any = None


class OfferingPatch(OfferingDocuments):
    name: str | None = Field(default=None, min_length=1)
    lifecycle_status: LifecycleStatus | None = None
    valid_for: TimePeriod | None = None
    description: str | None = None
    is_sellable: bool | None = None
    product_specification: SpecificationRef | None = None
    product_offering_price: list[Any] | None = None
    category: list[CategoryRef] | None = None


@dataclass
class NewPrice:
    """A price sent inline in an offering's productOfferingPrice[]."""

    data: PriceCreate
    documents: dict[str, Any]
    id: UUID | None
    path: str


PriceEntry = PriceRef | NewPrice


def price_entries(body: dict[str, Any]) -> tuple[list[PriceEntry], list[Problem]]:
    """productOfferingPrice[] (ProductOfferingPriceRefOrValue): an entry with `@type`
    ProductOfferingPriceRef, or with an `id` and no `@type`, refers to a price; one with
    `@type` ProductOfferingPrice, or with neither, is a new price, under POST's rules.
    The problems are reported under productOfferingPrice.<index>."""
    raw = body.get("productOfferingPrice")
    if not isinstance(raw, list):
        return [], []  # absent, null or not an array: the model's to report
    entries: list[PriceEntry] = []
    problems: list[Problem] = []
    for index, item in enumerate(raw):
        path = f"productOfferingPrice.{index}"
        if not isinstance(item, dict):
            problems.append((path, "must be an object", False))
            continue
        kind = item.get("@type")
        if kind == "ProductOfferingPriceRef" or (kind is None and "id" in item):
            try:
                entries.append(PriceRef.model_validate(item))
            except ValidationError as exc:
                found = validation_problems(exc.errors(include_url=False, include_context=False))
                problems += _prefixed(found, path)
            continue
        if kind not in (None, "ProductOfferingPrice"):
            problems.append(
                (f"{path}.@type", "must be one of: ProductOfferingPrice, ProductOfferingPriceRef",
                 False)
            )  # fmt: skip
            continue
        item = dict(item)
        try:
            new_id = take_id(item)
        except TmfError:  # the 422 of take_id, as a problem of this entry
            problems.append((f"{path}.id", "must be a UUID (this server's ids are UUIDs)", False))
            continue
        data, documents, invalid, found = check_documented(
            PriceCreate, item, PRICE_DOCUMENTS, API, "ProductOfferingPrice_FVO", path
        )
        problems += invalid + found
        if data is not None and data.price is None:
            problems.append(
                (f"{path}.price", "required for a price attached to an offering", False)
            )
        if data is not None:
            problems += _prefixed(tax_problems(documents.get("tax"), data.price), path)
            entries.append(NewPrice(data, documents, new_id, path))
    return entries, problems


def offering_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    spec_id = row["product_specification_id"]
    spec = None if spec_id is None else catalog.get_product_specification(conn, spec_id)
    prices = catalog.prices_of_offering(conn, row["id"])
    body = without_nulls(
        {
            "id": str(row["id"]),
            "href": _href("productOffering", row["id"]),
            "name": row["name"],
            "description": row["description"],
            "lifecycleStatus": row["lifecycle_status"],
            "isSellable": row["is_sellable"],
            "validFor": None if row["valid_for"] is None else time_period(row["valid_for"]),
            "productSpecification": None if spec is None else specification_ref(spec),
            "productOfferingPrice": [
                versioned(
                    ref(
                        "ProductOfferingPrice",
                        _href("productOfferingPrice", price["id"]),
                        price["id"],
                        price["name"],
                    ),
                    price,
                )  # fmt: skip
                for price in prices
            ],
            "category": [
                versioned(ref("Category", _href("category", c["id"]), c["id"], c["name"]), c)
                for c in classification.memberships(conn, "offering", row["id"])
            ],
            "lastUpdate": utc(row["updated_at"]),
        }
    )
    return {**body, **row["details"], "@type": "ProductOffering"} | meta_body(row)


def _check_offering_period(old: Range | None, new: Range | None, now: datetime) -> None:
    """An offering's validFor decides which start dates new orders may have
    (offeringNotValid); changing it in the past would rewrite what was orderable then.
    The start may not move into the past and the end may not be set or moved into the
    past; repeating the current value is not a change. No validFor is a start and an end
    at infinity. Billing never reads it, so no change here can leave a product without a
    price (prices: D-W18, D-W20)."""
    old_start, old_end = (None, None) if old is None else (old.lower, old.upper)
    new_start, new_end = (None, None) if new is None else (new.lower, new.upper)
    if new_start != old_start and (new_start is None or new_start < now):
        raise conflict(
            "validFor.startDateTime cannot be moved into the past; only to the present or"
            " the future"
        )
    if new_end != old_end and new_end is not None and new_end < now:
        raise conflict(
            "validFor.endDateTime cannot be set into the past; only to the present or the future"
        )


def _offering_code(documents: dict[str, Any]) -> str | None:
    """The code: the id of the externalIdentifier entry of type offeringCode (at most
    one), whose owner is bss (filled in when absent). Changes `documents` in place."""
    entries = documents.get("externalIdentifier") or []
    coded = [i for i, e in enumerate(entries) if e.get("externalIdentifierType") == CODE_TYPE]
    if len(coded) > 1:
        raise unprocessable("externalIdentifier: at most one entry may be the offeringCode")
    if not coded:
        return None
    entry = entries[coded[0]]
    try:
        code = OfferingCode.model_validate(
            {k: v for k, v in entry.items() if k in ("id", "externalIdentifierType", "owner")}
        )
    except ValidationError as exc:
        found = validation_problems(exc.errors(include_url=False, include_context=False))
        raise problems_error(_prefixed(found, f"externalIdentifier.{coded[0]}")) from None
    entry["owner"] = code.owner
    return code.id


def _ever_ordered(conn: psycopg.Connection, offering_id: UUID) -> bool:
    return any(count_references(conn, offering_id, ORDERED).values())


def _specification(conn: psycopg.Connection, reference: SpecificationRef) -> None:
    """An existing specification, the one referred to; taken FOR SHARE (a delete of it
    waits for this offering)."""
    spec = catalog.lock_product_specification(conn, reference.id, share=True)
    if spec is None:
        raise unprocessable(f"productSpecification {reference.id} does not exist")
    check_version(reference, spec, "productSpecification")
    schema = reference.target_product_schema
    if schema is not None and not same(schema, spec["details"].get("targetProductSchema")):
        raise unprocessable(
            "productSpecification.targetProductSchema: must be the specification's own"
        )


def _categories(conn: psycopg.Connection, refs: list[CategoryRef]) -> list[UUID]:
    """Existing categories, each once; under classification.lock."""
    ids = [r.id for r in refs]
    if len(set(ids)) != len(ids):
        raise unprocessable("category: duplicate references are not allowed")
    for reference in refs:
        row = classification.get(conn, "category", reference.id)
        if row is None:
            raise unprocessable(f"category {reference.id} does not exist")
        check_version(reference, row, "category")
    return ids


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
    entries: list[PriceEntry],
    now: datetime,
    *,
    events: bool = True,
) -> None:
    """Attach the prices referred to and create (then attach) the new ones. `events`:
    a Create event for each new price (an import emits only its job's)."""
    for entry in entries:
        if isinstance(entry, NewPrice):
            claim_id(conn, "product_offering_price", entry.id)
            row = insert_price(conn, entry.data, entry.documents, now, entry.id)
            if events:
                emit.created(conn, API, "productOfferingPrice", price_body(row), at=now)
            price_id = row["id"]
        else:
            price = catalog.get_product_offering_price(conn, entry.id)
            if price is None:
                raise unprocessable(f"productOfferingPrice {entry.id} does not exist")
            check_version(entry, price, "productOfferingPrice")
            if price["product_offering_id"] == offering_id:
                continue  # already attached: a merge patch repeats the whole list
            if price["product_offering_id"] is not None:
                raise conflict(
                    f"productOfferingPrice {entry.id} already belongs to another offering"
                )
            if price["amount"] is None:
                raise conflict(
                    f"productOfferingPrice {entry.id} has no price: it cannot price an offering"
                )
            _not_in_the_past(entry.id, price["valid_for"].lower, now)
            price_id = entry.id
        try:
            with conn.transaction():
                candidate = catalog.get_product_offering_price(conn, price_id)
                assert candidate is not None
                _check_cadence(conn, offering_id, candidate)
                attached = catalog.attach_price(conn, price_id, offering_id)
        except pg_errors.ExclusionViolation:
            raise conflict(
                f"productOfferingPrice {price_id} overlaps a price of the same type and"
                " currency already on this offering"
            ) from None
        except pg_errors.CheckViolation:  # its price was removed meanwhile
            raise conflict(
                f"productOfferingPrice {price_id} has no price: it cannot price an offering"
            ) from None
        if attached is None:  # attached elsewhere between our read and the update
            raise conflict(f"productOfferingPrice {price_id} already belongs to another offering")


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
    uid = take_id(body)
    entries, found = price_entries(body)
    data, documents = parse_documented(
        OfferingCreate, body, OFFERING_DOCUMENTS, API, "ProductOffering_FVO", found
    )
    with tx() as conn:
        claim_id(conn, "product_offering", uid)
        row = insert_offering(conn, data, documents, entries, clock.now(), uid)
        new = offering_body(conn, row)
        emit.created(conn, API, "productOffering", new, at=clock.now())
    return created(listing.select_fields(new, fields))


def insert_offering(
    conn: psycopg.Connection,
    data: OfferingCreate,
    documents: dict[str, Any],
    entries: list[PriceEntry],
    now: datetime,
    new_id: UUID | None = None,
    *,
    events: bool = True,
) -> Row:
    """POST's rules and write, prices attached; an import passes the source's id (and
    the categories in its own step: `data.category` empty)."""
    if data.product_specification is not None:
        _specification(conn, data.product_specification)
    code = _offering_code(documents)
    if data.category:
        classification.lock(conn)  # category memberships are written under it
        category_ids = _categories(conn, data.category)
    try:
        with conn.transaction():
            row = catalog.create_product_offering(
                conn,
                product_specification_id=None
                if data.product_specification is None
                else data.product_specification.id,
                name=data.name,
                lifecycle_status=data.lifecycle_status,
                valid_for=None if data.valid_for is None else data.valid_for.to_range(),
                is_sellable=data.is_sellable,
                description=data.description,
                code=code,
                details=merge_documents({}, documents),
                id=new_id,
            )
    except pg_errors.UniqueViolation as exc:
        if exc.diag.constraint_name == "product_offering_pkey":  # an import's id, raced
            raise conflict(f"productOffering {new_id} already exists") from None
        raise conflict(f"offering code {code} is already in use") from None
    if data.category:
        classification.replace(conn, "offering", row["id"], category_ids)
    _attach_prices(conn, row["id"], entries, now, events=events)
    return with_meta(conn, "product_offering", row, data)


@router.patch("/productOffering/{offering_id}")
def patch_offering(
    offering_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, OFFERING_ATTRIBUTES)
    check_type(body, "ProductOffering")
    _read_only(body, "lastUpdate")
    entries, found = price_entries(body)
    data, documents = parse_documented(
        OfferingPatch, body, OFFERING_DOCUMENTS, API, "ProductOffering_MVO", found
    )
    not_null(data, "name", "lifecycle_status", "is_sellable", "product_offering_price", "category")
    sent = data.model_fields_set
    changes = columns(
        data,
        exclude=_document_fields(OFFERING_DOCUMENTS)
        | {"valid_for", "product_offering_price", "product_specification", "category"},
    )
    if "valid_for" in sent:
        changes["valid_for"] = None if data.valid_for is None else data.valid_for.to_range()
    with tx() as conn:
        uid = parse_id("productOffering", offering_id)
        if data.category is not None:
            classification.lock(conn)  # before the offering, as a delete takes them
        current = catalog.lock_offering(conn, uid)  # a concurrent delete goes first or waits
        if current is None:
            raise not_found("productOffering", offering_id)
        if data.lifecycle_status is not None:
            _check_lifecycle("offering", current["lifecycle_status"], data.lifecycle_status)
        old = emit.before(conn, API, "productOffering", lambda: offering_body(conn, current))
        if "valid_for" in changes:
            _check_offering_period(current["valid_for"], changes["valid_for"], clock.now())
        if "product_specification" in sent:
            _change_specification(conn, current, data.product_specification)
            spec = data.product_specification
            changes["product_specification_id"] = None if spec is None else spec.id
        if documents:
            details = merge_documents(current["details"], documents)
            if "externalIdentifier" in documents:
                changes["code"] = _offering_code(details)
            changes["details"] = details
        category_ids = None if data.category is None else _categories(conn, data.category)
        if category_ids is not None and not changes:
            # membership is part of this resource: fire its lastUpdate trigger too
            changes["name"] = current["name"]
        if data.product_offering_price is not None:
            # merge patch replaces the whole array, so a missing entry would be a detach
            wanted = {e.id for e in entries if isinstance(e, PriceRef)}
            dropped = [p for p in catalog.prices_of_offering(conn, uid) if p["id"] not in wanted]
            if dropped:
                raise conflict(
                    "a price cannot be detached from an offering (end its validFor instead):"
                    f" {', '.join(str(p['id']) for p in dropped)}"
                )
            _attach_prices(conn, uid, entries, clock.now())
        try:
            with conn.transaction():
                row = catalog.update_product_offering(conn, uid, changes)
        except pg_errors.UniqueViolation:
            raise conflict(f"offering code {changes.get('code')} is already in use") from None
        if category_ids is not None:
            classification.replace(conn, "offering", uid, category_ids)
        patched = offering_body(conn, with_meta(conn, "product_offering", row, data))
        emit.changed(
            conn, API, "productOffering", old, patched, at=clock.now(), state="lifecycleStatus"
        )
    return TmfJSONResponse(listing.select_fields(patched, fields))


def _change_specification(
    conn: psycopg.Connection, current: Row, reference: SpecificationRef | None
) -> None:
    """A product's productSpecification is its offering's: once the offering was
    ordered, its specification stays (409). null removes it."""
    if reference is not None and reference.id == current["product_specification_id"]:
        _specification(conn, reference)  # the same one: check the version sent
        return
    if _ever_ordered(conn, current["id"]):
        raise conflict(
            "productSpecification cannot change: the offering was ordered, and its products'"
            " productSpecification is the offering's"
        )
    if reference is not None:
        _specification(conn, reference)


# --- deletes ---
# Only what nothing references is deleted (409 otherwise, naming the references). An
# offering that was ever ordered keeps itself and all its prices, so billing history and
# future bills never change; such an entry is retired instead.

ORDERED = {
    "product": ("product", "product_offering_id"),
    "productOrderItem": ("product_order_item", "product_offering_id"),
}


@router.delete("/productSpecification/{spec_id}", status_code=204, response_class=Response)
def delete_specification(spec_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productSpecification", spec_id)
        row = catalog.lock_product_specification(conn, uid)
        if row is None:
            raise not_found("productSpecification", spec_id)
        counts = count_references(
            conn, uid, {"productOffering": ("product_offering", "product_specification_id")}
        )
        refuse_if_referenced("productSpecification", uid, counts, "delete those offerings first")
        emit.deleted(conn, API, "productSpecification", specification_body(row), at=clock.now())
        delete_row(conn, "product_specification", uid)
    return Response(status_code=204)


@router.delete("/productOfferingPrice/{price_id}", status_code=204, response_class=Response)
def delete_price(price_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productOfferingPrice", price_id)
        price = catalog.get_product_offering_price(conn, uid)
        if price is None:
            raise not_found("productOfferingPrice", price_id)
        offering_id = price["product_offering_id"]
        if offering_id is None:
            if not catalog.delete_unattached_price(conn, uid):
                raise conflict(f"productOfferingPrice {uid} was attached meanwhile; try again")
            emit.deleted(conn, API, "productOfferingPrice", price_body(price), at=clock.now())
            return Response(status_code=204)
        # An attached price never moves. Lock its offering first, as price changes do;
        # orders hold it FOR SHARE, so none is in flight.
        catalog.lock_offering(conn, offering_id)
        counts = count_references(conn, offering_id, ORDERED)
        refuse_if_referenced(
            "productOfferingPrice",
            uid,
            {f"{label} of its offering": n for label, n in counts.items()},
            "a price of an ordered offering stays; end its validFor instead",
        )
        emit.deleted(conn, API, "productOfferingPrice", price_body(price), at=clock.now())
        delete_row(conn, "product_offering_price", uid)
    return Response(status_code=204)


@router.delete("/productOffering/{offering_id}", status_code=204, response_class=Response)
def delete_offering(offering_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("productOffering", offering_id)
        classification.lock(conn)  # category memberships are written under it
        row = catalog.lock_offering(conn, uid)
        if row is None:
            raise not_found("productOffering", offering_id)
        ordered = count_references(conn, uid, ORDERED)
        refuse_if_referenced(
            "productOffering", uid, ordered, "retire it instead (lifecycleStatus Retired)"
        )
        prices = count_references(
            conn, uid, {"productOfferingPrice": ("product_offering_price", "product_offering_id")}
        )
        refuse_if_referenced("productOffering", uid, prices, "delete its prices first")
        emit.deleted(conn, API, "productOffering", lambda: offering_body(conn, row), at=clock.now())
        classification.remove_memberships(conn, "offering", uid)
        delete_row(conn, "product_offering", uid)
    return Response(status_code=204)
