"""TMF654 v4 transferBalance: credit moved between two prepaid buckets. docs/decisions/prepaid.md.

A POST validates both sides under the two accounts' row locks (taken in id order), holds the
sender's principal - plus the fee when the originator pays it - and queues one `transfer`
command, answering 201 `created`. No principal moves here: the billing worker completes the
transfer later (P4h), so a fee can be posted by billing. A transfer is final (the v4 update
has no status), so there is no cancelling; a correction is a new opposite transfer. A
business refusal (`sameBucket`, `currency`, a number or product that is not the
account's, a closed or suspended account, too few spendable funds) is stored `failed`,
answered 201, and sends a Failure event; a malformed or unknown reference is a 4xx that
stores nothing.
"""

import copy
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field, model_validator

from app.api import listing
from app.api.account import account_href
from app.api.balance import BASE, bucket_href, quantity
from app.api.common import (
    Money,
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
    ProductRef,
    Quantity,
    check_references,
)
from app.billing.funding import InsufficientFunds
from app.events import emit
from app.repository import balance_actions as repo
from app.repository import buckets, funding, idempotency, parties
from app.repository import prepaid_commands as commands
from app.repository._sql import Row, fetch_all

API = "TMF654"
RESOURCE = "transferBalance"
KIND = "transfer"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent and checked against the spec.
DOCUMENTS = ("channel", "requestor", "validFor", "receiver")
ATTRIBUTES = (
    "amount",
    "usageType",
    "bucket",
    "receiverBucket",
    "receiverBucketUsageType",
    "partyAccount",
    "logicalResource",
    "receiverLogicalResource",
    "product",
    "receiverProduct",
    "transferCost",
    "costOwner",
    "reason",
    "description",
    "status",
    "requestedDate",
    "confirmationDate",
    *DOCUMENTS,
)
# What a PATCH may not change once the transfer was accepted: sent, each must equal what is
# stored. The stored request is immutable (balance_action_retained), so the channel,
# requestor and receiver cannot be edited either; only `reason` can.
IMMUTABLE = tuple(a for a in ATTRIBUTES if a not in ("reason", "description", "status"))
SERVER_SET = ("status", "requestedDate", "confirmationDate")
# TransferBalance_Update skips these: a client echoing the resource back may repeat them.
PATCH_SERVER_SET = (*SERVER_SET, "validFor", "description", "partyAccount")

Documents = documents_model("TransferBalanceDocuments", API, DOCUMENTS)
PatchDocuments = documents_model("TransferBalancePatchDocuments", API, DOCUMENTS)


class TransferCreate(Documents):
    reason: str
    description: str | None = None
    amount: Quantity
    usage_type: str
    bucket: BucketRef
    receiver_bucket: BucketRef
    receiver_bucket_usage_type: str
    logical_resource: list[LogicalResourceRef] = Field(min_length=1)
    receiver_logical_resource: LogicalResourceRef
    product: list[ProductRef] = []
    receiver_product: ProductRef | None = None
    transfer_cost: Money | None = None
    cost_owner: Literal["originator", "receiver"] | None = None

    @model_validator(mode="after")
    def _supported(self) -> "TransferCreate":
        if self.channel is None:
            raise ValueError("channel is required")
        if (self.usage_type, self.receiver_bucket_usage_type) != ("monetary", "monetary"):
            raise ValueError("usageType and receiverBucketUsageType must be monetary")
        if self.amount.amount <= 0:
            raise ValueError("amount.amount must be positive")
        cost = self.transfer_cost
        if cost is not None:
            if cost.unit != self.amount.units:
                raise ValueError("transferCost.unit must be the amount's currency")
            if cost.value < 0:
                raise ValueError("transferCost.value must not be negative")
            if self.cost_owner == "receiver" and cost.value > self.amount.amount:
                raise ValueError("a receiver-paid transferCost cannot exceed the amount")
        return self


class TransferPatch(PatchDocuments):
    """TransferBalance_Update: `reason`, `channel`, `logicalResource` and
    `receiverLogicalResource` are required; only `reason` changes, the rest is repeated as
    stored (checked by the handler). There is no status: a transfer is final."""

    reason: str
    logical_resource: list[LogicalResourceRef] = Field(min_length=1)
    receiver_logical_resource: LogicalResourceRef
    amount: Quantity | None = None
    usage_type: str | None = None
    bucket: BucketRef | None = None
    receiver_bucket: BucketRef | None = None
    receiver_bucket_usage_type: str | None = None
    product: list[ProductRef] | None = None
    receiver_product: ProductRef | None = None
    transfer_cost: Money | None = None
    cost_owner: Literal["originator", "receiver"] | None = None

    @model_validator(mode="after")
    def _channel(self) -> "TransferPatch":
        if self.channel is None:
            raise ValueError("channel is required")
        return self


def transfer_href(action_id) -> str:
    return f"{BASE}/{RESOURCE}/{action_id}"


def transfer_body(conn: psycopg.Connection, row: Row) -> dict:
    """The transfer as TMF654 TransferBalance. `reason` is the client's (it is required); a
    refused one states why in `description`, in place of the client's own. The fee and who
    pays it are always stated (zero, originator)."""
    request = row["request"]
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    body = {
        "id": str(row["id"]),
        "href": transfer_href(row["id"]),
        "@type": request.get("@type") or "TransferBalance",
        "status": row["status"],
        "amount": quantity(row["amount"], row["currency"]),
        "usageType": "monetary",
        "receiverBucketUsageType": "monetary",
        "transferCost": {"unit": row["currency"], "value": row["transfer_cost"]},
        "costOwner": row["cost_owner"],
        "reason": row["reason"],
        "description": row["failure_reason"] or row["description"],
        "requestedDate": utc(row["requested_at"]),
        "confirmationDate": utc(row["confirmed_at"]) if row["confirmed_at"] else None,
        "bucket": ref("Bucket", bucket_href(row["bucket_id"]), row["bucket_id"]),
        "receiverBucket": ref(
            "Bucket", bucket_href(row["receiver_bucket_id"]), row["receiver_bucket_id"]
        ),
        "partyAccount": ref(
            "BillingAccount",
            account_href(account["id"]),
            account["id"],
            account["name"],
            ref_type="PartyAccountRef",
        )
        | without_nulls({"description": account["description"], "status": account["state"]}),
    }
    body["bucket"]["@type"] = body["receiverBucket"]["@type"] = "BucketRef"
    names = ("logicalResource", "receiverLogicalResource", "product", "receiverProduct")
    for name in (*names, *DOCUMENTS, "@baseType", "@schemaLocation"):
        if request.get(name) is not None:
            body[name] = request[name]
    return without_nulls(body)


def _receiver_references(conn: psycopg.Connection, data: TransferCreate, receiver: Row, today):
    """422 for a receiver number or product that is unknown; otherwise the refusal (None:
    accepted) for one that is not the receiving account's."""
    live = buckets.live_products(conn, receiver["billing_account_id"], today)
    refused = None
    if data.receiver_product is not None:
        found = fetch_all(conn, "SELECT id FROM product WHERE id = %s", (data.receiver_product.id,))
        if not found:
            raise unprocessable(f"receiverProduct {data.receiver_product.id} does not exist")
        if data.receiver_product.id not in {p["id"] for p in live}:
            refused = "receiverProduct is not a live product of the receiving account"
    number_id = data.receiver_logical_resource.id
    if not fetch_all(conn, "SELECT id FROM subscriber_number WHERE id = %s", (number_id,)):
        raise unprocessable(f"receiverLogicalResource {number_id} does not exist")
    held = buckets.numbers(conn, [p["id"] for p in live], today)
    if number_id not in {n["id"] for n in held}:
        refused = "number: receiverLogicalResource is not a number of the receiving account"
    return refused


def _refusal(
    conn: psycopg.Connection,
    data: TransferCreate,
    sender: Row,
    receiver: Row,
    today,
) -> str | None:
    """422 for a reference that is malformed or unknown; otherwise why a valid transfer is
    refused (None: accepted), first the sender's own references and state."""
    sender_refusal = check_references(conn, data, sender, today)
    receiver_refusal = _receiver_references(conn, data, receiver, today)
    if sender_refusal:
        return sender_refusal
    if sender["account_state"] == "Suspended":
        return "billing account is suspended: it cannot send a transfer"
    if sender["id"] == receiver["id"]:
        return "sameBucket: the receiver bucket is the sender's"
    if receiver["currency"] != sender["currency"]:
        return f"currency: the receiver bucket is in {receiver['currency']}"
    if receiver["account_state"] == "Closed":
        return "the receiving account is closed"
    return receiver_refusal


@router.get(f"/{RESOURCE}")
def list_transfer_balances(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("id", listing.as_uuid),
            "status": ("status", listing.as_str),
            "bucket.id": ("bucket_id", listing.as_uuid),
            "receiverBucket.id": ("receiver_bucket_id", listing.as_uuid),
            "partyAccount.id": ("billing_account_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = repo.page(conn, query.where, query.offset, query.limit, kind=KIND)
        bodies = [transfer_body(conn, row) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get(f"/{RESOURCE}/{{transfer_id}}")
def get_transfer_balance(transfer_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, transfer_id), kind=KIND)
        if row is None:
            raise not_found(RESOURCE, transfer_id)
        body = transfer_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post(f"/{RESOURCE}")
def create_transfer_balance(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    body_sent = copy.deepcopy(body)  # the request kept and hashed is what the client sent
    check_type(body, "TransferBalance")
    server_set = take_server_set(body, SERVER_SET)
    data, _ = parse_documented(TransferCreate, body, DOCUMENTS, API, "TransferBalance_Create")
    key = idempotency.header(request)
    operator = request.state.operator
    with tx() as conn:
        if key is not None:
            digest = idempotency.request_hash(list(request.query_params.multi_items()), body_sent)
            replay = idempotency.begin(conn, operator, "POST", request.url.path, key, digest)
            if replay is not None:
                return idempotency.replayed(replay)
        sender = repo.bucket(conn, data.bucket.id)
        if sender is None:
            raise unprocessable(f"bucket {data.bucket.id} does not exist")
        receiver = repo.bucket(conn, data.receiver_bucket.id)
        if receiver is None:
            raise unprocessable(f"receiverBucket {data.receiver_bucket.id} does not exist")
        now, today = clock.now(), clock.today()
        # both accounts' row locks, in id order, so opposite transfers cannot deadlock
        for account_id in sorted({sender["billing_account_id"], receiver["billing_account_id"]}):
            parties.get_billing_account(conn, account_id, "update")
        sender = repo.bucket(conn, sender["id"]) or sender  # their state, under the locks
        receiver = repo.bucket(conn, receiver["id"]) or receiver
        sender_id = sender["billing_account_id"]
        cost = data.transfer_cost.value if data.transfer_cost else Decimal("0.00")
        owner = data.cost_owner or "originator"
        repo.release_expired(conn, sender_id, now)
        refused = _refusal(conn, data, sender, receiver, today)
        held = data.amount.amount + (cost if owner == "originator" else Decimal(0))
        if not refused:
            try:
                funding.plan(conn, sender_id, held)
            except InsufficientFunds as e:
                refused = f"transfer exceeds the spendable funds ({max(Decimal(0), e.available)})"
        action_id = uuid4()
        hold_id = command_id = None
        if not refused:
            # a fresh command identity nobody else can contend for, so taking it after the
            # account locks cannot deadlock
            command_id = uuid4()
            commands.enqueue(
                conn,
                command_id,
                sender_id,
                kind="transfer",
                due_date=today,
                request={"action_id": str(action_id)},
                now=now,
            )
            hold_id = funding.reserve_for_command(
                conn, sender_id, held, command_id=command_id, held_at=now
            )
        row = repo.insert_transfer(
            conn,
            sender,
            receiver,
            action_id=action_id,
            request=body_sent,
            amount=data.amount.amount,
            cost=cost,
            cost_owner=owner,
            operator=operator,
            now=now,
            reason=data.reason,
            description=data.description,
            hold_id=hold_id,
            command_id=command_id,
            failure=refused,
        )
        new = transfer_body(conn, row)
        check_server_set(server_set, new)
        emit.emit(conn, API, RESOURCE, "Failure" if refused else "Create", new, at=now)
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=now,
            )  # fmt: skip
    return created(selected)


@router.patch(f"/{RESOURCE}/{{transfer_id}}")
def patch_transfer_balance(
    transfer_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    """Only `reason` changes; everything else may only be repeated as stored (409
    otherwise). A transfer has no status to change and is never cancelled."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "TransferBalance")
    sent = copy.deepcopy(body)
    server_set = take_server_set(body, PATCH_SERVER_SET)
    data, _ = parse_documented(TransferPatch, body, DOCUMENTS, API, "TransferBalance_Update")
    with tx() as conn:
        uid = parse_id(RESOURCE, transfer_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, transfer_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True, kind=KIND)
        assert row is not None
        before = transfer_body(conn, row)
        for name in IMMUTABLE:
            if sent.get(name) is not None and not same(sent[name], before.get(name)):
                raise conflict(f"{name} cannot change once the transfer was accepted")
        check_server_set(server_set, before)
        row = repo.set_descriptive(conn, uid, {"reason": data.reason}, kind=KIND)
        new = transfer_body(conn, row)
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(f"/{RESOURCE}/{{transfer_id}}", status_code=204, response_class=Response)
def delete_transfer_balance(
    transfer_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    """Only a failed transfer: it moved and held nothing. An accepted one is final."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE, transfer_id)
        found = repo.get(conn, uid, kind=KIND)
        if found is None:
            raise not_found(RESOURCE, transfer_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True, kind=KIND)
        if row is None:
            raise not_found(RESOURCE, transfer_id)
        if row["status"] != "failed":
            raise conflict(
                f"transferBalance {uid} is {row['status']}: only a failed transfer can be"
                " deleted (an accepted one is final)"
            )
        repo.delete_failed(conn, uid)
        idempotency.tombstone(conn, transfer_href(uid), now=clock.now())
    return Response(status_code=204)
