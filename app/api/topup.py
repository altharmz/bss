"""TMF654 v4 topupBalance: money received on a prepaid bucket. docs/decisions/prepaid.md.

A top-up is a `balance_action` that records a done TMF676 payment, written in the same
transaction under the account's row lock. A payment posted directly to a prepaid account
records a completed top-up too (`record_payment`), so the history is complete whichever API
took the money. Cancelling appends a compensating entry (never touching the payment) and
only while the payment is entirely unused. A business refusal (a closed account, a
number or product that is not the account's) is stored `failed`, answered 201, and sends a
Failure event; a malformed or unknown reference is a 4xx that stores nothing.
"""

import copy
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from psycopg import errors as pg_errors
from pydantic import Field, field_validator, model_validator

from app.api import listing
from app.api.account import account_href
from app.api.balance import BASE, bucket_href, quantity
from app.api.common import (
    CENT,
    MONEY_LIMIT,
    Ref,
    TmfModel,
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
from app.api.payment import PaymentCreate, post_payment
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import balance_actions as repo
from app.repository import buckets, idempotency, parties
from app.repository._sql import Row, fetch_all

API = "TMF654"
RESOURCE = "topupBalance"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent and checked against the spec.
DOCUMENTS = ("channel", "requestor", "validFor", "balanceTopup", "paymentMethod")
ATTRIBUTES = (
    "amount",
    "usageType",
    "bucket",
    "partyAccount",
    "logicalResource",
    "product",
    "reason",
    "description",
    "isAutoTopup",
    "status",
    "requestedDate",
    "confirmationDate",
    *DOCUMENTS,
)
# What a PATCH may not change once the top-up exists: sent, each must equal what is stored.
IMMUTABLE = (
    *(a for a in ATTRIBUTES if a not in ("reason", "description", "status")),
    "relatedParty",
)
SERVER_SET = ("status", "requestedDate", "confirmationDate")
PATCH_SERVER_SET = ("requestedDate", "confirmationDate")
UNSPECIFIED_METHOD = "Unspecified recorded receipt"
LIVE = "(termination_date IS NULL OR termination_date > %(today)s)"


class Quantity(TmfModel):
    """A monetary quantity: exact cents, in a currency (`units`)."""

    amount: Decimal
    units: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("amount", mode="before")
    @classmethod
    def _exact_cents(cls, value: Any) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ValueError("must be a JSON number")
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) >= MONEY_LIMIT:
            raise ValueError("out of range for an amount")
        if amount.quantize(CENT) != amount:
            raise ValueError("must have at most 2 decimal places (amounts are never rounded)")
        return amount.quantize(CENT)


class BucketRef(Ref):
    referred_type = "Bucket"


class PartyAccountRef(Ref):
    referred_type = "BillingAccount"
    description: str | None = None
    status: str | None = None


class LogicalResourceRef(Ref):
    referred_type = "LogicalResource"


class ProductRef(Ref):
    referred_type = "Product"


PATCH_DOCUMENTS = (*DOCUMENTS, "relatedParty")
Documents = documents_model("TopupBalanceDocuments", API, DOCUMENTS)
PatchDocuments = documents_model("TopupBalancePatchDocuments", API, PATCH_DOCUMENTS)


class TopupCreate(Documents):
    amount: Quantity
    usage_type: str
    bucket: BucketRef
    party_account: PartyAccountRef
    reason: str | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] = []
    product: list[ProductRef] = []
    is_auto_topup: bool | None = None
    number_of_periods: int | None = None
    recurring_period: str | None = None
    voucher: str | None = None

    @model_validator(mode="after")
    def _supported(self) -> "TopupCreate":
        if self.usage_type != "monetary":
            raise ValueError("usageType must be monetary (bundles are out of scope)")
        if self.amount.amount <= 0:
            raise ValueError("amount.amount must be positive")
        if self.is_auto_topup:
            raise ValueError("isAutoTopup is not supported (no payment collection)")
        if self.recurring_period is not None or self.number_of_periods is not None:
            raise ValueError("recurringPeriod and numberOfPeriods are not supported")
        if self.voucher is not None:
            raise ValueError("voucher is not supported (no voucher management)")
        return self


class TopupPatch(PatchDocuments):
    """TopupBalance_Update: `status` is required; only reason and description change, the
    rest is repeated as stored (checked by the handler), and `cancelled` reverses."""

    status: Literal["created", "failed", "cancelled", "completed"]
    amount: Quantity | None = None
    usage_type: str | None = None
    bucket: BucketRef | None = None
    party_account: PartyAccountRef | None = None
    reason: str | None = None
    description: str | None = None
    logical_resource: list[LogicalResourceRef] | None = None
    product: list[ProductRef] | None = None
    is_auto_topup: bool | None = None
    number_of_periods: int | None = None
    recurring_period: str | None = None
    voucher: str | None = None


def topup_href(action_id: Any) -> str:
    return f"{BASE}/{RESOURCE}/{action_id}"


def topup_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    """The top-up as TMF654 TopupBalance. `reason` is the client's; a refused top-up
    without one states why it was refused."""
    request = row["request"]
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    body = {
        "id": str(row["id"]),
        "href": topup_href(row["id"]),
        "@type": request.get("@type") or "TopupBalance",
        "status": row["status"],
        "amount": quantity(row["amount"], row["currency"]),
        "usageType": "monetary",
        "isAutoTopup": False,
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
        ),
    }
    body["bucket"]["@type"] = "BucketRef"
    for name in (
        "logicalResource",
        "product",
        *DOCUMENTS,
        "@baseType",
        "@schemaLocation",
    ):
        if request.get(name) is not None:
            body[name] = request[name]
    return without_nulls(body)


def _unknown(name: str, ids: list[UUID]) -> Exception:
    return unprocessable(f"{name} {', '.join(map(str, ids))} does not exist")


def check_references(conn, data, bucket: Row, today) -> str | None:
    """422 for a reference that is malformed, unknown or contradicts another; otherwise
    the refusal (None: accepted) for a valid reference that is not the account's. `data`
    is any create model with amount, product and logical_resource."""
    if data.amount.units != bucket["currency"]:
        raise unprocessable(
            f"amount.units {data.amount.units} does not match the bucket's currency"
            f" {bucket['currency']}"
        )
    refused = None
    products = {p.id for p in data.product}
    numbers = {n.id for n in data.logical_resource}
    live = buckets.live_products(conn, bucket["billing_account_id"], today)
    if products:
        known = {
            r["id"]
            for r in fetch_all(conn, "SELECT id FROM product WHERE id = ANY(%s)", (list(products),))
        }
        if known != products:
            raise _unknown("product", sorted(products - known))
        if not products <= {p["id"] for p in live}:
            refused = "product is not a live product of this account"
    if numbers:
        known = {
            r["id"]
            for r in fetch_all(
                conn, "SELECT id FROM subscriber_number WHERE id = ANY(%s)", (list(numbers),)
            )
        }
        if known != numbers:
            raise _unknown("logicalResource", sorted(numbers - known))
        held = buckets.numbers(conn, [p["id"] for p in live], today)
        if not numbers <= {n["id"] for n in held}:
            refused = "logicalResource is not a number of this account"
    if bucket["account_state"] == "Closed":
        refused = "billing account is closed"
    return refused


def _method(documents: dict[str, Any]) -> tuple[str, str]:
    """The generated payment's name and @type: the method sent, never implied collected."""
    sent = documents.get("paymentMethod")
    if not sent:
        return UNSPECIFIED_METHOD, "PaymentMethodRef"
    return sent.get("name") or str(sent["id"]), sent.get("@type") or "PaymentMethodRef"


def _payment_for(account: Row, amount: Decimal, name: str, at_type: str) -> PaymentCreate:
    return PaymentCreate.model_validate(
        {
            "account": {"id": str(account["id"])},
            "totalAmount": {"unit": account["currency"], "value": amount},
            "paymentMethod": {"name": name, "@type": at_type},
        }
    )


def record_payment(
    conn: psycopg.Connection, payment: Row, payment_body: dict[str, Any], *, operator: str, now
) -> None:
    """The completed top-up of a payment posted straight to a prepaid account (a no-op on
    an account without a bucket, which only test fixtures have until prepaid is enabled)."""
    bucket = repo.account_bucket(conn, payment["billing_account_id"])
    if bucket is None:
        return
    request = {
        "@type": "TopupBalance",
        "amount": {"amount": payment["amount"], "units": payment["currency"]},
        "usageType": "monetary",
        "bucket": {"id": str(bucket["id"])},
        "partyAccount": {"id": str(payment["billing_account_id"])},
    }
    row = repo.insert_topup(
        conn,
        bucket,
        request=request,
        amount=payment["amount"],
        operator=operator,
        now=now,
        reason="Payment recorded through TMF676",
        description=f"TMF676 payment {payment['id']} ({payment_body['paymentMethod'].get('name')})",
        payment_id=payment["id"],
    )
    emit.created(conn, API, RESOURCE, lambda: topup_body(conn, row), at=now)


@router.get(f"/{RESOURCE}")
def list_topup_balances(request: Request, tx: Tx) -> TmfJSONResponse:
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
        rows, total = repo.page(conn, query.where, query.offset, query.limit)
        bodies = [topup_body(conn, row) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get(f"/{RESOURCE}/{{topup_id}}")
def get_topup_balance(topup_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, topup_id))
        if row is None:
            raise not_found(RESOURCE, topup_id)
        body = topup_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post(f"/{RESOURCE}")
def create_topup_balance(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    body_sent = copy.deepcopy(body)  # the request kept and hashed is what the client sent
    check_type(body, "TopupBalance")
    server_set = take_server_set(body, SERVER_SET)
    data, documents = parse_documented(TopupCreate, body, DOCUMENTS, API, "TopupBalance_Create")
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
        if data.party_account.id != bucket["billing_account_id"]:
            raise unprocessable("partyAccount must be the billing account that owns the bucket")
        refused = check_references(conn, data, bucket, clock.today())
        common = {
            "request": body_sent,
            "amount": data.amount.amount,
            "operator": operator,
            "now": now,
            "reason": data.reason,
            "description": data.description,
        }
        if refused:
            row = repo.insert_topup(conn, bucket, failure=refused, **common)
        else:
            name, at_type = _method(documents)
            payment, _ = post_payment(
                conn,
                account,
                _payment_for(account, data.amount.amount, name, at_type),
                {},
                now=now,
                operator=operator,
                record_topup=False,
            )
            row = repo.insert_topup(conn, bucket, payment_id=payment["id"], **common)
        new = topup_body(conn, row)
        check_server_set(server_set, new)
        emit.emit(conn, API, RESOURCE, "Failure" if refused else "Create", new, at=now)
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=now,
            )  # fmt: skip
    return created(selected)


@router.patch(f"/{RESOURCE}/{{topup_id}}")
def patch_topup_balance(
    topup_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """`status: cancelled` reverses a completed top-up whose money is entirely unspent and
    unreserved (409 otherwise); `reason` and `description` may change; everything else
    may only be repeated. A failed, cancelled or otherwise final status cannot change."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "TopupBalance")
    sent = copy.deepcopy(body)
    server_set = take_server_set(body, PATCH_SERVER_SET)
    data, _ = parse_documented(TopupPatch, body, PATCH_DOCUMENTS, API, "TopupBalance_Update")
    operator = request.state.operator
    with tx() as conn:
        uid = parse_id(RESOURCE, topup_id)
        found = repo.get(conn, uid)
        if found is None:
            raise not_found(RESOURCE, topup_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True)
        assert row is not None
        before = topup_body(conn, row)
        for name in IMMUTABLE:
            if sent.get(name) is not None and not same(sent[name], before.get(name)):
                raise conflict(f"{name} cannot change once the top-up was accepted")
        check_server_set(server_set, before)
        for name in ("voucher", "numberOfPeriods", "recurringPeriod"):
            if sent.get(name) is not None:
                raise unprocessable(f"{name} is not supported")
        now = clock.now()
        if data.status != row["status"]:
            if not (row["status"] == "completed" and data.status == "cancelled"):
                raise conflict(f"a {row['status']} top-up cannot become {data.status}")
            if repo.payment_in_use(conn, row["payment_id"]):
                raise conflict(
                    "the top-up's money is spent, allocated or held: it cannot be reversed"
                )
        changes = {
            column: getattr(data, column)
            for column in ("reason", "description")
            if column in data.model_fields_set
        }
        row = repo.set_descriptive(conn, uid, changes)
        if data.status != before["status"]:
            row = repo.cancel_topup(conn, row, operator=operator, now=now)
            try:
                repo.check_bounds(conn)
            except pg_errors.CheckViolation:
                raise conflict("the top-up's money is in use: it cannot be reversed") from None
        new = topup_body(conn, row)
        if data.status != before["status"]:
            emit.emit(conn, API, RESOURCE, "Cancel", new, at=now)
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(f"/{RESOURCE}/{{topup_id}}", status_code=204, response_class=Response)
def delete_topup_balance(topup_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only a failed top-up: it moved no money. A completed or cancelled one is history."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE, topup_id)
        found = repo.get(conn, uid)
        if found is None:
            raise not_found(RESOURCE, topup_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        row = repo.get(conn, uid, lock=True)
        if row is None:
            raise not_found(RESOURCE, topup_id)
        if row["status"] != "failed":
            raise conflict(
                f"topupBalance {uid} is {row['status']}: only a failed top-up can be deleted"
                " (a completed one can be cancelled instead)"
            )
        repo.delete_failed(conn, uid)
        idempotency.tombstone(conn, topup_href(uid), now=clock.now())
    return Response(status_code=204)
