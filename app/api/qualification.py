"""TMF679 v5 Product Offering Qualification: one eligibility check before an order.
docs/decisions/qualification.md.

`checkProductOfferingQualification` asks "can this billing account get these offerings, with
this device, number and address?"; `queryProductOfferingQualification` asks "which offerings
can it get?". Both are answered synchronously (state `done`, instantSyncQualification true)
by a dry run of the order engine (app/ordering/qualification.py) and stored as answered: GET
list and by id, POST, PATCH (only what describes it: description, channel, note, relatedParty,
requestedQualificationCompletionDate), DELETE and `/hub` (app/api/hub.py). The worker's
housekeeping deletes a qualification 30 days after its creation.

A check item is a TMF622 order item in TMF679 clothes: `action` (default add),
`productOffering`, `product` (id, billingAccount, productCharacteristic imei/simType, place,
realizingResource, productRelationship, status - exactly what an order item's product
takes), `qualificationItemRelationship` and `expectedActivationDate` (the order's
requestedStartDate: one for all items; default today). An item is `qualified` when the order
engine names no reason against it nor against the order as a whole; each reason is an
eligibilityResultReason {code, label}: the productOrderErrorMessage code and reason. No
alternates are proposed (`provideAlternative` is kept as sent).

A query's searchCriteria names the billing account (`product.billingAccount`), optionally a
device, an add-on pack's plan and the like (`product`) or one offering (`productOffering`).
Each Active, sellable offering is qualified as an add item; it is listed when nothing but a
missing input (a number, a SIM, an address, a pack's plan: `NEEDS_INPUT`) stands against it.
The engine checks prepaid funds only once nothing else stands against an order, so a plan that
needs a number is listed whatever the balance; the check with the number answers fully.
"""

from datetime import datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime, Field

from app.api import listing
from app.api.account import account_ref, check_rated
from app.api.catalog import offering_ref
from app.api.common import (
    ProductOfferingRef,
    RatedBillingAccountRef,
    TmfModel,
    billing_tz,
    check_server_set,
    check_type,
    claim_id,
    created,
    documents_model,
    merge_documents,
    meta_body,
    meta_columns,
    parse_documented,
    parse_id,
    take_id,
    take_server_set,
    to_billing_date,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.inventory import product_href
from app.api.ordering import ItemProduct, OrderItemIn, item_request
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.events.types import BASES
from app.ordering import qualification as engine
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest
from app.repository import catalog, parties
from app.repository import qualification as repo
from app.repository._sql import Row

API = "TMF679"
BASE = BASES[API]
CHECK = "checkProductOfferingQualification"
QUERY = "queryProductOfferingQualification"
TYPES = {CHECK: "CheckProductOfferingQualification", QUERY: "QueryProductOfferingQualification"}
KINDS = {CHECK: "check", QUERY: "query"}
CHECK_ITEMS = "checkProductOfferingQualificationItem"
router = APIRouter(prefix=BASE)

# what describes a qualification: stored as sent (details), changeable by PATCH
DOCUMENTS = (
    "description",
    "channel",
    "note",
    "relatedParty",
    "requestedQualificationCompletionDate",
)
# a check's flags: they shape the answer, so they are kept as sent and never change
FLAGS = ("provideAlternative", "provideOnlyAvailable", "provideResultReason")
SERVER_SET = ("instantSyncQualification",)  # always true: every qualification is synchronous
ANSWER = (
    "state",
    "creationDate",
    "effectiveQualificationDate",
    "expectedQualificationCompletionDate",
    "expirationDate",
    "instantSyncQualification",
)
ATTRIBUTES = {
    CHECK: (*ANSWER, *DOCUMENTS, "qualificationResult", *FLAGS, CHECK_ITEMS),
    QUERY: (*ANSWER, *DOCUMENTS, "searchCriteria", "qualifiedProductOfferingItem"),
}
# what PATCH cannot change: the question and its answer
READ_ONLY = {
    CHECK: (*ANSWER, "qualificationResult", *FLAGS, CHECK_ITEMS),
    QUERY: (*ANSWER, "searchCriteria", "qualifiedProductOfferingItem"),
}
# a query qualifies at most this many offerings; a bigger catalog names one in searchCriteria
MAX_CANDIDATES = 100

CheckDocuments = documents_model("CheckQualificationDocuments", API, DOCUMENTS)
QueryDocuments = documents_model("QueryQualificationDocuments", API, DOCUMENTS)


class QualificationProduct(ItemProduct):
    """An item's product: what a TMF622 order item's product takes, and the billing account
    (TMF679 has no other place for it)."""

    billing_account: RatedBillingAccountRef | None = None


class ItemRelationship(TmfModel):
    id: str = Field(min_length=1)
    relationship_type: str = Field(min_length=1)


class CheckItemIn(TmfModel):
    id: str | None = Field(default=None, min_length=1)
    action: Literal["add", "modify", "delete", "noChange"] = "add"
    product_offering: ProductOfferingRef | None = None
    product: QualificationProduct | None = None
    qualification_item_relationship: list[ItemRelationship] | None = None
    expected_activation_date: AwareDatetime | None = None


class CheckCreate(CheckDocuments):  # type: ignore[misc,valid-type]
    check_product_offering_qualification_item: list[CheckItemIn] = Field(min_length=1)
    provide_alternative: bool | None = None
    provide_only_available: bool | None = None
    provide_result_reason: bool | None = None


class CheckPatch(CheckDocuments):  # type: ignore[misc,valid-type]
    pass


class SearchCriteria(TmfModel):
    id: str | None = Field(default=None, min_length=1)
    product_offering: ProductOfferingRef | None = None
    product: QualificationProduct


class QueryCreate(QueryDocuments):  # type: ignore[misc,valid-type]
    search_criteria: SearchCriteria


class QueryPatch(QueryDocuments):  # type: ignore[misc,valid-type]
    pass


# --- rows -> bodies ---


def href(resource: str, uid: Any) -> str:
    return f"{BASE}/{resource}/{uid}"


def qualification_body(row: Row) -> dict[str, Any]:
    resource = CHECK if row["kind"] == "check" else QUERY
    details = row["details"]
    body: dict[str, Any] = {
        "id": str(row["id"]),
        "href": href(resource, row["id"]),
        "state": row["state"],
        "creationDate": utc(row["created_at"]),
        "effectiveQualificationDate": utc(row["created_at"]),
        "expectedQualificationCompletionDate": utc(row["created_at"]),
        "expirationDate": utc(row["expires_at"]),
        "instantSyncQualification": True,
        **{name: details.get(name) for name in DOCUMENTS},
    }
    if resource == CHECK:
        body["qualificationResult"] = row["qualification_result"]
        body |= {name: details.get(name) for name in FLAGS}
        body[CHECK_ITEMS] = row["items"]
    else:
        body["searchCriteria"] = details["searchCriteria"]
        body["qualifiedProductOfferingItem"] = row["items"]
    body["@type"] = TYPES[resource]
    return without_nulls(body) | meta_body(row)


def _product_body(conn: psycopg.Connection, product: QualificationProduct) -> dict[str, Any]:
    """The item's product as sent, with its references and @type filled in."""
    body = product.model_dump(by_alias=True, exclude_none=True, mode="json")
    if product.billing_account is not None:
        account = parties.get_billing_account(conn, product.billing_account.id)
        assert account is not None  # the dry run refused a missing one
        body["billingAccount"] = account_ref(account)
    if product.id is not None:
        body["href"] = product_href(product.id)
    # TMF679's ProductRefOrValue is a Product or a ProductRef (its discriminator); TMF622
    # clients send "ProductRefOrValue", which names neither
    values = set(body) - {"id", "href", "name", "@type", "@referredType"}
    body["@type"] = "Product" if values else "ProductRef"
    if values:
        body.pop("@referredType", None)
    else:
        body["@referredType"] = "Product"
    return body


def _offering_ref(conn: psycopg.Connection, offering_id: UUID) -> dict[str, Any]:
    offering = catalog.get_product_offering(conn, offering_id)
    assert offering is not None  # the dry run refused a missing one
    return offering_ref(offering)


# --- the dry run ---


def _order_item(
    item_id: str,
    action: str,
    offering: ProductOfferingRef | None,
    product: QualificationProduct | None,
    documents: dict[str, Any],
    path: str,
) -> ItemRequest:
    """The order item a qualification item stands for, checked as POST productOrder checks
    one (the same 4xx for the same mistakes)."""
    order_item = OrderItemIn.model_construct(
        id=item_id,
        action=action,
        quantity=1,
        product_offering=offering,
        product=product,
        billing_account=None if product is None else product.billing_account,
        product_order_item=None,
    )
    return item_request(order_item, None, documents, path)


def _verdict(
    conn: psycopg.Connection, request: OrderRequest, now: datetime, today: Any
) -> engine.Verdict:
    try:
        return engine.qualify(conn, request, now=now, today=today)
    except OrderRefused as exc:
        raise unprocessable(str(exc)) from None


def _expiry(now: datetime) -> datetime:
    """The end of the qualification's day (BILLING_TZ): prices, funds and numbers move."""
    day = now.astimezone(billing_tz()).date() + timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=billing_tz())


def _start(items: list[CheckItemIn]) -> Any:
    """The order's start date: the items' expectedActivationDate (one for all), else today."""
    days = {
        to_billing_date(i.expected_activation_date) for i in items if i.expected_activation_date
    }
    if len(days) > 1:
        raise unprocessable(
            f"{CHECK_ITEMS}.expectedActivationDate: the items are qualified as one order, which"
            " starts on one day; send one date for all of them"
        )
    return days.pop() if days else None


def _item_body(
    conn: psycopg.Connection,
    item_id: str,
    item: CheckItemIn,
    reasons: list[Any],
    *,
    with_reasons: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {"id": item_id, "action": item.action}
    if item.product_offering is not None:
        body["productOffering"] = _offering_ref(conn, item.product_offering.id)
    if item.product is not None:
        body["product"] = _product_body(conn, item.product)
    if item.qualification_item_relationship:
        body["qualificationItemRelationship"] = [
            {
                "id": r.id,
                "relationshipType": r.relationship_type,
                "@type": "ProductOfferingQualificationItemRelationship",
            }
            for r in item.qualification_item_relationship
        ]
    if item.expected_activation_date is not None:
        body["expectedActivationDate"] = utc(item.expected_activation_date)
    body["qualificationItemResult"] = "unqualified" if reasons else "qualified"
    body["state"] = "done"
    if reasons and with_reasons:
        body["eligibilityResultReason"] = [
            {"code": p.code, "label": p.reason, "@type": "EligibilityResultReason"} for p in reasons
        ]
    body["@type"] = "CheckProductOfferingQualificationItem"
    return body


def _details(data: TmfModel, documents: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    flags = {name: getattr(data, _snake(name)) for name in names}
    return without_nulls({**documents, **flags})


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name)


# --- shared handlers ---


def _list(resource: str, request: Request, tx: Tx) -> TmfJSONResponse:
    filters: dict[str, tuple[str, listing.Converter]] = {
        "id": ("id", listing.as_uuid),
        "state": ("state", listing.as_str),
        "billingAccount.id": ("billing_account_id", listing.as_uuid),
    }
    if resource == CHECK:
        filters["qualificationResult"] = ("qualification_result", listing.as_str)
    query = listing.parse_list_query(request, filters=filters, attributes=ATTRIBUTES[resource])
    with tx() as conn:
        rows, more = repo.page(conn, KINDS[resource], query.where, query.offset, query.limit)
    return listing.list_response([qualification_body(row) for row in rows], more, query)


def _get(resource: str, raw_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES[resource])
    with tx() as conn:
        row = repo.get(conn, KINDS[resource], parse_id(resource, raw_id))
        if row is None:
            raise not_found(resource, raw_id)
    return TmfJSONResponse(listing.select_fields(qualification_body(row), fields))


def _patch_checked(resource: str, body: dict[str, Any]) -> None:
    check_type(body, TYPES[resource])
    for name in READ_ONLY[resource]:
        if name in body:
            raise bad_request(f"{name} is read-only")


def _patch(
    resource: str,
    raw_id: str,
    data: TmfModel,
    documents: dict[str, Any],
    request: Request,
    tx: Tx,
    clock: Any,
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES[resource])
    now = clock.now()
    with tx() as conn:
        uid = parse_id(resource, raw_id)
        row = repo.get(conn, KINDS[resource], uid, lock=True)
        if row is None:
            raise not_found(resource, raw_id)
        old = emit.before(conn, API, resource, lambda: qualification_body(row))
        changes: dict[str, Any] = meta_columns(data)
        details = merge_documents(row["details"], documents)
        if details != row["details"]:
            changes["details"] = details
        if changes:
            row = repo.update(conn, uid, changes | {"updated_at": now})
        new = qualification_body(row)
        emit.changed(conn, API, resource, old, new, at=now, state="state")
    return TmfJSONResponse(listing.select_fields(new, fields))


def _delete(resource: str, raw_id: str, request: Request, tx: Tx, clock: Any) -> Response:
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(resource, raw_id)
        row = repo.get(conn, KINDS[resource], uid, lock=True)
        if row is None:
            raise not_found(resource, raw_id)
        gone = qualification_body(row)
        repo.delete(conn, uid)
        emit.deleted(conn, API, resource, gone, at=clock.now())
    return Response(status_code=204)


# --- checkProductOfferingQualification ---


@router.get(f"/{CHECK}")
def list_qualification_checks(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list(CHECK, request, tx)


@router.get(f"/{CHECK}/{{qualification_id}}")
def get_qualification_check(qualification_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get(CHECK, qualification_id, request, tx)


@router.post(f"/{CHECK}")
def create_qualification_check(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES[CHECK])
    check_type(body, TYPES[CHECK])
    uid = take_id(body)
    server_set = take_server_set(body, SERVER_SET)
    data, documents = parse_documented(CheckCreate, body, DOCUMENTS, API, f"{TYPES[CHECK]}_FVO")
    items: list[CheckItemIn] = data.check_product_offering_qualification_item
    ids = [item.id or str(index + 1) for index, item in enumerate(items)]
    if len(set(ids)) != len(ids):
        raise unprocessable(f"{CHECK_ITEMS} ids must be unique within the qualification")
    if not any(i.product and (i.product.billing_account or i.product.id) for i in items):
        raise unprocessable(
            "a qualification needs a billing account: product.billingAccount on an item, or"
            " the product of a modify, delete or noChange item"
        )
    order = OrderRequest(
        billing_account_id=None,
        items=tuple(
            _order_item(
                item_id,
                item.action,
                item.product_offering,
                item.product,
                {
                    "productOrderItemRelationship": [
                        {"id": r.id, "relationshipType": r.relationship_type}
                        for r in item.qualification_item_relationship
                    ]
                }
                if item.qualification_item_relationship
                else {},
                f"{CHECK_ITEMS}.{index}",
            )
            for index, (item_id, item) in enumerate(zip(ids, items, strict=True))
        ),
        requested_start=_start(items),
    )
    now, today = clock.now(), clock.today()
    with tx() as conn:
        claim_id(conn, repo.TABLE, uid)
        for item in items:
            check_rated(conn, item.product.billing_account if item.product else None)
        verdict = _verdict(conn, order, now, today)
        answers = [
            _item_body(
                conn,
                item_id,
                item,
                verdict.of(item_id),
                with_reasons=data.provide_result_reason is not False,
            )
            for item_id, item in zip(ids, items, strict=True)
        ]
        result = "qualified" if not verdict.problems else "unqualified"
        if data.provide_only_available:
            answers = [a for a in answers if a["qualificationItemResult"] == "qualified"]
        row = repo.insert(
            conn,
            uid=uid,
            kind="check",
            billing_account_id=verdict.account_id,
            qualification_result=result,
            qualification_date=order.requested_start or today,
            now=now,
            expires_at=_expiry(now),
            items=answers,
            details=_details(data, documents, FLAGS),
        )
        row = with_meta(conn, repo.TABLE, row, data)
        new = qualification_body(row)
        check_server_set(server_set, new)
        emit.created(conn, API, CHECK, new, at=now)
    return created(listing.select_fields(new, fields))


@router.patch(f"/{CHECK}/{{qualification_id}}")
def patch_qualification_check(
    qualification_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Changes what describes the check; its question and answer are read-only."""
    _patch_checked(CHECK, body)
    data, documents = parse_documented(CheckPatch, body, DOCUMENTS, API, f"{TYPES[CHECK]}_MVO")
    return _patch(CHECK, qualification_id, data, documents, request, tx, clock)


@router.delete(f"/{CHECK}/{{qualification_id}}", status_code=204, response_class=Response)
def delete_qualification_check(
    qualification_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    return _delete(CHECK, qualification_id, request, tx, clock)


# --- queryProductOfferingQualification ---


@router.get(f"/{QUERY}")
def list_qualification_queries(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list(QUERY, request, tx)


@router.get(f"/{QUERY}/{{qualification_id}}")
def get_qualification_query(qualification_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get(QUERY, qualification_id, request, tx)


@router.post(f"/{QUERY}")
def create_qualification_query(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES[QUERY])
    check_type(body, TYPES[QUERY])
    uid = take_id(body)
    server_set = take_server_set(body, SERVER_SET)
    data, documents = parse_documented(QueryCreate, body, DOCUMENTS, API, f"{TYPES[QUERY]}_FVO")
    criteria: SearchCriteria = data.search_criteria
    account_ref_sent = criteria.product.billing_account
    if account_ref_sent is None:
        raise unprocessable("searchCriteria.product.billingAccount: the account to qualify for")
    now, today = clock.now(), clock.today()
    with tx() as conn:
        claim_id(conn, repo.TABLE, uid)
        check_rated(conn, account_ref_sent)
        if parties.get_billing_account(conn, account_ref_sent.id) is None:
            raise unprocessable(f"billingAccount {account_ref_sent.id} does not exist")
        if criteria.product_offering is not None:
            candidates = [criteria.product_offering.id]
        else:
            candidates = repo.sellable_offerings(conn, MAX_CANDIDATES + 1)
            if len(candidates) > MAX_CANDIDATES:
                raise unprocessable(
                    f"more than {MAX_CANDIDATES} offerings are for sale: name one in"
                    " searchCriteria.productOffering"
                )
        qualified = []
        for offering_id in candidates:
            item = _order_item(
                "1",
                "add",
                ProductOfferingRef(id=offering_id),
                criteria.product,
                {},
                "searchCriteria",
            )
            verdict = _verdict(
                conn, OrderRequest(billing_account_id=None, items=(item,)), now, today
            )
            if all(p.code in engine.NEEDS_INPUT for p in verdict.problems):
                qualified.append(offering_id)
        answers = [
            {
                "id": str(index),
                "productOffering": _offering_ref(conn, offering_id),
                "@type": "QueryProductOfferingQualificationItem",
            }
            for index, offering_id in enumerate(qualified, 1)
        ]
        search = criteria.model_dump(by_alias=True, exclude_none=True, mode="json")
        search["product"] = _product_body(conn, criteria.product)
        if criteria.product_offering is not None:
            search["productOffering"] = _offering_ref(conn, criteria.product_offering.id)
        search["@type"] = "QueryProductOfferingQualificationItem"
        row = repo.insert(
            conn,
            uid=uid,
            kind="query",
            billing_account_id=account_ref_sent.id,
            qualification_result=None,
            qualification_date=today,
            now=now,
            expires_at=_expiry(now),
            items=answers,
            details={**_details(data, documents, ()), "searchCriteria": search},
        )
        row = with_meta(conn, repo.TABLE, row, data)
        new = qualification_body(row)
        check_server_set(server_set, new)
        emit.created(conn, API, QUERY, new, at=now)
    return created(listing.select_fields(new, fields))


@router.patch(f"/{QUERY}/{{qualification_id}}")
def patch_qualification_query(
    qualification_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Changes what describes the query; its question and answer are read-only."""
    _patch_checked(QUERY, body)
    data, documents = parse_documented(QueryPatch, body, DOCUMENTS, API, f"{TYPES[QUERY]}_MVO")
    return _patch(QUERY, qualification_id, data, documents, request, tx, clock)


@router.delete(f"/{QUERY}/{{qualification_id}}", status_code=204, response_class=Response)
def delete_qualification_query(
    qualification_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    return _delete(QUERY, qualification_id, request, tx, clock)
