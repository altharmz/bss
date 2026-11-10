"""TMF654 v4 adjustBalance: a signed correction of a prepaid bucket. docs/decisions/prepaid.md.

A positive amount credits the bucket with one adjustment funding source; a negative one
debits it, limited to the spendable funds (ledger less holds and unpaid charges) and
consuming specific sources in funding order. Both are `balance_action` rows written under
the account's row lock. Cancelling appends a compensating entry: a credit's source is
consumed (refused with 409 once spent or held), a debit restores each source it consumed,
once. A business refusal (a closed account, a debit above the spendable funds, a number or
product that is not the account's) is stored `failed`, answered 201, and sends a Failure
event; a malformed or unknown reference is a 4xx that stores nothing.
"""

import copy
from decimal import Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Request, Response
from psycopg import errors as pg_errors
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
from app.repository._sql import Row

API = "TMF654"
RESOURCE = "adjustBalance"
KIND = "adjustment"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent and checked against the spec.
DOCUMENTS = ("channel", "requestor", "validFor")
ATTRIBUTES = (
    "amount",
    "usageType",
    "adjustType",
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
# What a PATCH may not change once the adjustment exists: sent, each must equal what is stored.
IMMUTABLE = (
    *(a for a in ATTRIBUTES if a not in ("reason", "description", "status")),
    "relatedParty",
)
SERVER_SET = ("status", "requestedDate", "confirmationDate")
PATCH_SERVER_SET = ("requestedDate", "confirmationDate")

PATCH_DOCUMENTS = (*DOCUMENTS, "relatedParty")
Documents = documents_model("AdjustBalanceDocuments", API, DOCUMENTS)
PatchDocuments = documents_model("AdjustBalancePatchDocuments", API, PATCH_DOCUMENTS)


class AdjustCreate(Documents):
    amount: Quantity
    usage_type: str
    bucket: BucketRef
    adjust_type: Literal["recurring", "oneTime"] | None = None
    reason: str | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] = []
    product: list[ProductRef] = []

    @model_validator(mode="after")
    def _supported(self) -> "AdjustCreate":
        if self.usage_type != "monetary":
            raise ValueError("usageType must be monetary (bundles are out of scope)")
        if self.amount.amount == 0:
            raise ValueError("amount.amount must not be zero")
        if self.adjust_type == "recurring":
            raise ValueError("adjustType recurring is not supported (one-time corrections only)")
        return self


class AdjustPatch(PatchDocuments):
    """AdjustBalance_Update: `status` is required; only reason and description change, the
    rest is repeated as stored (checked by the handler), and `cancelled` reverses."""

    status: Literal["created", "failed", "cancelled", "completed"]
    amount: Quantity | None = None
    usage_type: str | None = None
    adjust_type: Literal["recurring", "oneTime"] | None = None
    bucket: BucketRef | None = None
    party_account: PartyAccountRef | None = None
    reason: str | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] | None = None
    product: list[ProductRef] | None = None


def adjust_href(action_id) -> str:
    return f"{BASE}/{RESOURCE}/{action_id}"


def adjust_body(conn: psycopg.Connection, row: Row) -> dict:
    """The adjustment as TMF654 AdjustBalance. `reason` is the client's; a refused one
    without it states why."""
    request = row["request"]
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    body = {
        "id": str(row["id"]),
        "href": adjust_href(row["id"]),
        "@type": request.get("@type") or "AdjustBalance",
        "status": row["status"],
        "amount": quantity(row["amount"], row["currency"]),
        "usageType": "monetary",
        "adjustType": "oneTime",
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
    for name in ("adjustType", "logicalResource", "product", *DOCUMENTS, "@baseType",
                 "@schemaLocation"):  # fmt: skip
        if request.get(name) is not None:
            body[name] = request[name]
    return without_nulls(body)


def _debit_plan(conn: psycopg.Connection, account_id, amount: Decimal):
    """The sources a debit of `amount` consumes, or the refusal text."""
    try:
        return funding.plan(conn, account_id, amount).sources, None
    except InsufficientFunds as e:
        return (), f"debit exceeds the spendable funds ({max(Decimal(0), e.available)})"


@router.get(f"/{RESOURCE}")
def list_adjust_balances(request: Request, tx: Tx) -> TmfJSONResponse:
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
        bodies = [adjust_body(conn, row) for row in rows]
    return listing.list_response(bodies, more, query)


@router.get(f"/{RESOURCE}/{{adjust_id}}")
def get_adjust_balance(adjust_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, adjust_id), kind=KIND)
        if row is None:
            raise not_found(RESOURCE, adjust_id)
        body = adjust_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post(f"/{RESOURCE}")
def create_adjust_balance(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    body_sent = copy.deepcopy(body)  # the request kept and hashed is what the client sent
    check_type(body, "AdjustBalance")
    server_set = take_server_set(body, SERVER_SET)
    data, _ = parse_documented(AdjustCreate, body, DOCUMENTS, API, "AdjustBalance_Create")
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
        # the account's row lock first, as a payment takes it
        account = parties.get_billing_account(conn, bucket["billing_account_id"], "update")
        assert account is not None  # foreign key
        bucket = repo.bucket(conn, data.bucket.id) or bucket  # its state, under the lock
        refused = check_references(conn, data, bucket, clock.today())
        amount = data.amount.amount
        sources = ()
        if not refused and amount < 0:
            repo.release_expired(conn, bucket["billing_account_id"], now)
            sources, refused = _debit_plan(conn, bucket["billing_account_id"], -amount)
        row = repo.insert_adjustment(
            conn,
            bucket,
            request=body_sent,
            amount=amount,
            operator=operator,
            now=now,
            reason=data.reason,
            description=data.description,
            sources=sources,
            failure=refused,
        )
        new = adjust_body(conn, row)
        check_server_set(server_set, new)
        emit.emit(conn, API, RESOURCE, "Failure" if refused else "Create", new, at=now)
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=now,
            )  # fmt: skip
    return created(selected)


@router.patch(f"/{RESOURCE}/{{adjust_id}}")
def patch_adjust_balance(
    adjust_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """`status: cancelled` reverses a completed adjustment (a credit only while its money
    is unspent and unreserved: 409 otherwise); `reason` and `description` may change;
    everything else may only be repeated. A failed, cancelled or final status cannot change."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "AdjustBalance")
    sent = copy.deepcopy(body)
    server_set = take_server_set(body, PATCH_SERVER_SET)
    data, _ = parse_documented(AdjustPatch, body, PATCH_DOCUMENTS, API, "AdjustBalance_Update")
    operator = request.state.operator
    with tx() as conn:
        uid = parse_id(RESOURCE, adjust_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, adjust_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True, kind=KIND)
        assert row is not None
        before = adjust_body(conn, row)
        for name in IMMUTABLE:
            if sent.get(name) is not None and not same(sent[name], before.get(name)):
                raise conflict(f"{name} cannot change once the adjustment was accepted")
        check_server_set(server_set, before)
        now = clock.now()
        if data.status != row["status"]:
            if not (row["status"] == "completed" and data.status == "cancelled"):
                raise conflict(f"a {row['status']} adjustment cannot become {data.status}")
        changes = {
            column: getattr(data, column)
            for column in ("reason", "description")
            if column in data.model_fields_set
        }
        row = repo.set_descriptive(conn, uid, changes, kind=KIND)
        if data.status != before["status"]:
            row = repo.cancel_adjustment(conn, row, operator=operator, now=now)
            try:
                repo.check_bounds(conn)
            except pg_errors.CheckViolation:
                raise conflict("the adjustment's money is in use: it cannot be reversed") from None
        new = adjust_body(conn, row)
        if data.status != before["status"]:
            emit.emit(conn, API, RESOURCE, "Cancel", new, at=now)
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(f"/{RESOURCE}/{{adjust_id}}", status_code=204, response_class=Response)
def delete_adjust_balance(adjust_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a failed adjustment: it moved no money. A completed or cancelled one is history."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE, adjust_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, adjust_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True, kind=KIND)
        if row is None:
            raise not_found(RESOURCE, adjust_id)
        if row["status"] != "failed":
            raise conflict(
                f"adjustBalance {uid} is {row['status']}: only a failed adjustment can be"
                " deleted (a completed one can be cancelled instead)"
            )
        repo.delete_failed(conn, uid)
        idempotency.tombstone(conn, adjust_href(uid), now=clock.now())
    return Response(status_code=204)
