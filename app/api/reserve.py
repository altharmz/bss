"""TMF654 v4 reserveBalance: an operator hold on a prepaid bucket. docs/decisions/prepaid.md.

A client-created hold pins the amount's funding sources under the account's row lock, so
no order, renewal, debit, refund or settlement can spend it. It ends when the client
cancels it or its `validFor.endDateTime` passes: the next spend under the lock, or the
worker's wake-up, releases it. Holds an order or renewal owns are not this resource's to
change (409). A business refusal (a closed account, an amount above the spendable funds, a
number or product that is not the account's) is stored `failed`, answered 201, and sends a
Failure event; a malformed or unknown reference is a 4xx that stores nothing.
"""

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import model_validator

from app.api import listing
from app.api.account import account_href
from app.api.balance import BASE, bucket_href, quantity
from app.api.common import (
    check_server_set,
    check_type,
    created,
    documents_model,
    parse_documented,
    parse_id,
    ref,
    same,
    take_server_set,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.api.topup import (
    BucketRef,
    LogicalResourceRef,
    PartyAccountRef,
    ProductRef,
    Quantity,
    check_references,
)
from app.billing.funding import InsufficientFunds
from app.events import emit
from app.repository import balance_actions as repo
from app.repository import funding, idempotency, parties
from app.repository._sql import Row, rows_by_id

API = "TMF654"
RESOURCE = "reserveBalance"
KIND = "reservation"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent and checked against the spec.
DOCUMENTS = ("channel", "requestor", "validFor")
ATTRIBUTES = (
    "amount",
    "usageType",
    "bucket",
    "partyAccount",
    "logicalResource",
    "product",
    "reason",
    "description",
    "status",
    "requestedDate",
    "confirmationDate",
    *DOCUMENTS,
)
# What a PATCH may not change once the hold exists: sent, each must equal what is stored.
IMMUTABLE = (
    *(a for a in ATTRIBUTES if a not in ("reason", "description", "status")),
    "relatedParty",
)
SERVER_SET = ("status", "requestedDate", "confirmationDate")
PATCH_SERVER_SET = ("requestedDate", "confirmationDate")

PATCH_DOCUMENTS = (*DOCUMENTS, "relatedParty")
Documents = documents_model("ReserveBalanceDocuments", API, DOCUMENTS)
PatchDocuments = documents_model("ReserveBalancePatchDocuments", API, PATCH_DOCUMENTS)


class ReserveCreate(Documents):
    amount: Quantity
    usage_type: str
    bucket: BucketRef
    party_account: PartyAccountRef
    reason: str | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] = []
    product: list[ProductRef] = []

    @model_validator(mode="after")
    def _supported(self) -> "ReserveCreate":
        if self.usage_type != "monetary":
            raise ValueError("usageType must be monetary (bundles are out of scope)")
        if self.amount.amount <= 0:
            raise ValueError("amount.amount must be positive")
        return self


class ReservePatch(PatchDocuments):
    """ReserveBalance_Update: `status` and `reason` are required; only reason and
    description change, the rest is repeated as stored (checked by the handler), and
    `cancelled` releases the hold."""

    status: Literal["created", "failed", "cancelled", "completed"]
    reason: str
    amount: Quantity | None = None
    usage_type: str | None = None
    bucket: BucketRef | None = None
    party_account: PartyAccountRef | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] | None = None
    product: list[ProductRef] | None = None


def reserve_href(action_id) -> str:
    return f"{BASE}/{RESOURCE}/{action_id}"


def reserve_body(conn: psycopg.Connection, row: Row) -> dict:
    return reserve_bodies(conn, [row])[0]


def reserve_bodies(
    conn: psycopg.Connection, rows: list[Row], accounts: dict | None = None
) -> list[dict]:
    """The bodies of a whole page: its billing accounts are read once (#302), or come
    from the caller (the history, which reads them once for every kind)."""
    if accounts is None:
        accounts = rows_by_id(conn, "billing_account", [r["billing_account_id"] for r in rows])
    return [_reserve_body(row, accounts[row["billing_account_id"]]) for row in rows]


def _reserve_body(row: Row, account: Row) -> dict:
    """The hold as TMF654 ReserveBalance. `reason` is the client's; a refused one without
    it states why."""
    request = row["request"]
    body = {
        "id": str(row["id"]),
        "href": reserve_href(row["id"]),
        "@type": request.get("@type") or "ReserveBalance",
        "status": row["status"],
        "amount": quantity(row["amount"], row["currency"]),
        "usageType": "monetary",
        "reason": row["reason"] or row["failure_reason"],
        "description": row["description"],
        "requestedDate": utc(row["requested_at"]),
        "confirmationDate": utc(row["confirmed_at"]) if row["confirmed_at"] else None,
        "bucket": ref("Bucket", bucket_href(row["bucket_id"]), row["bucket_id"]),
        "partyAccount": ref(
            "BillingAccount",
            account_href(account["id"]),
            account["id"],
            account["name"],
            ref_type="PartyAccountRef",
        )
        | without_nulls({"description": account["description"], "status": account["state"]}),
    }
    body["bucket"]["@type"] = "BucketRef"
    for name in ("logicalResource", "product", *DOCUMENTS, "@baseType", "@schemaLocation"):
        if request.get(name) is not None:
            body[name] = request[name]
    return without_nulls(body)


def _check_validity(request: dict, now) -> None:
    """422 unless `validFor` can be honoured: it starts now (no start, or one already
    reached) and ends in the future. A zone-less date-time is UTC."""
    period = request.get("validFor") or {}
    try:
        end = repo.expiry(request)
        start = period.get("startDateTime")
        started = datetime.fromisoformat(start) if start else None
    except ValueError:
        raise unprocessable("validFor must hold ISO 8601 date-times") from None
    if end is not None and end <= now:
        raise unprocessable("validFor.endDateTime must be in the future")
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started > now:
            raise unprocessable("a hold starts when it is accepted: validFor.startDateTime is past")


@router.get(f"/{RESOURCE}")
def list_reserve_balances(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("id", listing.as_uuid),
            "status": ("status", listing.as_str),
            "bucket.id": ("bucket_id", listing.as_uuid),
            "partyAccount.id": ("billing_account_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, more = repo.page(conn, query.where, query.offset, query.limit, kind=KIND)
        bodies = reserve_bodies(conn, rows)
    return listing.list_response(bodies, more, query)


@router.get(f"/{RESOURCE}/{{reserve_id}}")
def get_reserve_balance(reserve_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, reserve_id), kind=KIND)
        if row is None:
            raise not_found(RESOURCE, reserve_id)
        body = reserve_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post(f"/{RESOURCE}")
def create_reserve_balance(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    body_sent = copy.deepcopy(body)  # the request kept and hashed is what the client sent
    check_type(body, "ReserveBalance")
    server_set = take_server_set(body, SERVER_SET)
    data, _ = parse_documented(ReserveCreate, body, DOCUMENTS, API, "ReserveBalance_Create")
    key = idempotency.header(request)
    operator = request.state.operator
    with tx() as conn:
        if key is not None:
            digest = idempotency.request_hash(list(request.query_params.multi_items()), body_sent)
            replay = idempotency.begin(conn, operator, "POST", request.url.path, key, digest)
            if replay is not None:
                return idempotency.replayed(replay)
        bucket = repo.bucket(conn, data.bucket.id)
        if bucket is None:
            raise unprocessable(f"bucket {data.bucket.id} does not exist")
        now = clock.now()
        _check_validity(body_sent, now)
        # the account's row lock first, as a payment takes it
        account = parties.get_billing_account(conn, bucket["billing_account_id"], "update")
        assert account is not None  # foreign key
        bucket = repo.bucket(conn, data.bucket.id) or bucket  # its state, under the lock
        if data.party_account.id != bucket["billing_account_id"]:
            raise unprocessable("partyAccount must be the billing account that owns the bucket")
        repo.release_expired(conn, bucket["billing_account_id"], now)
        refused = check_references(conn, data, bucket, clock.today())
        hold_id = None
        if not refused:
            try:
                hold_id = funding.reserve(
                    conn, bucket["billing_account_id"], data.amount.amount, held_at=now
                )
            except InsufficientFunds as e:
                refused = f"hold exceeds the spendable funds ({max(Decimal(0), e.available)})"
        row = repo.insert_reservation(
            conn,
            bucket,
            request=body_sent,
            amount=data.amount.amount,
            operator=operator,
            now=now,
            reason=data.reason,
            description=data.description,
            hold_id=hold_id,
            failure=refused,
        )
        new = reserve_body(conn, row)
        check_server_set(server_set, new)
        emit.emit(conn, API, RESOURCE, "Failure" if refused else "Create", new, at=now)
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=now,
            )  # fmt: skip
    return created(selected)


@router.patch(f"/{RESOURCE}/{{reserve_id}}")
def patch_reserve_balance(
    reserve_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """`status: cancelled` releases a created hold (a hold an order or renewal owns is
    409); `reason` and `description` may change; everything else may only be repeated.
    A failed, cancelled or completed status cannot change."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "ReserveBalance")
    sent = copy.deepcopy(body)
    server_set = take_server_set(body, PATCH_SERVER_SET)
    data, _ = parse_documented(ReservePatch, body, PATCH_DOCUMENTS, API, "ReserveBalance_Update")
    with tx() as conn:
        uid = parse_id(RESOURCE, reserve_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, reserve_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        now = clock.now()
        repo.release_expired(conn, found["billing_account_id"], now)  # a lapsed hold is gone
        row = repo.get(conn, uid, lock=True, kind=KIND)
        assert row is not None
        before = reserve_body(conn, row)
        for name in IMMUTABLE:
            if sent.get(name) is not None and not same(sent[name], before.get(name)):
                raise conflict(f"{name} cannot change once the hold was accepted")
        check_server_set(server_set, before)
        if row["command_id"] is not None:
            raise conflict("the hold belongs to an order or renewal and is released with it")
        if data.status != row["status"] and not (
            row["status"] == "created" and data.status == "cancelled"
        ):
            raise conflict(f"a {row['status']} hold cannot become {data.status}")
        changes = {
            column: getattr(data, column)
            for column in ("reason", "description")
            if column in data.model_fields_set
        }
        row = repo.set_descriptive(conn, uid, changes, kind=KIND)
        if data.status != before["status"]:
            row = repo.cancel_reservation(conn, row, now=now)
        new = reserve_body(conn, row)
        if data.status != before["status"]:
            emit.emit(conn, API, RESOURCE, "Cancel", new, at=now)
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(f"/{RESOURCE}/{{reserve_id}}", status_code=204, response_class=Response)
def delete_reserve_balance(reserve_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a failed hold: it kept no money back. A created one is cancelled instead; a
    cancelled or completed one is history."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE, reserve_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, reserve_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True, kind=KIND)
        if row is None:
            raise not_found(RESOURCE, reserve_id)
        if row["status"] != "failed":
            raise conflict(
                f"reserveBalance {uid} is {row['status']}: only a failed hold can be deleted"
                " (a created one can be cancelled instead)"
            )
        repo.delete_failed(conn, uid)
        idempotency.tombstone(conn, reserve_href(uid), now=clock.now())
    return Response(status_code=204)
