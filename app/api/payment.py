"""TMF676 Payment Management v4: payment. (TMF676 has no v5.)

POST records a payment that has been received (`status: done`); there is no payment
processing here. `totalAmount` is what was received and what settles bills (they are
due with their taxes); `amount` and `taxAmount` are its split as the client states it
(`tax_part`), stored and answered.

The payment settles the account at once, under the billing account's row lock
(docs/archive/bill-lifecycle-design.md 6.4): first its `paymentItem[]` (explicit lettering to
named bills), then whatever is left goes to the oldest open bills. `paymentItem` in a
response is every allocation from the payment, summed per bill - it grows when the
payment's unused credit later pays bills issued after it.
"""

import copy
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Request
from psycopg import errors as pg_errors
from pydantic import Field, model_validator

from app.api import listing, specschema
from app.api.account import account_href
from app.api.bill import bill_href
from app.api.common import (
    BillingAccountRef,
    CustomerBillRef,
    Money,
    TmfModel,
    check_type,
    created,
    meta_body,
    money,
    parse_documented,
    parse_id,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import Problem, bad_request, conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.billing import settlement
from app.billing.adapter import settle_account
from app.events import emit
from app.repository import billing, idempotency, parties, payments
from app.repository._sql import Row

BASE = "/tmf-api/payment/v4"
router = APIRouter(prefix=BASE)

# Documents to us: stored as sent, checked against the spec (docs/decisions/tmf-api.md,
# "The last gaps").
DOCUMENTS = ("name", "description", "authorizationCode", "correlatorId", "channel", "payer")
ATTRIBUTES = (
    "account",
    "amount",
    "totalAmount",
    "taxAmount",
    "paymentDate",
    "paymentMethod",
    "status",
    "statusDate",
    "paymentItem",
    *DOCUMENTS,
)
# What the model keeps of a paymentMethod; the rest of it is stored as sent.
METHOD_KEYS = ("name", "@type")


def take_documents(
    body: dict[str, Any], documents: tuple[str, ...], schema: str
) -> tuple[dict[str, Any], list[Problem]]:
    """A payment's or refund's documents, taken out of `body` and checked against the
    spec's `schema`: its own `documents`, the `account` attributes beyond the reference
    it resolves to (`description`), and the `paymentMethod` as sent (the model keeps its
    name and @type). Returns what to store and the problems found."""
    sent = {name: body.pop(name) for name in documents if name in body}
    account = body.get("account")
    if isinstance(account, dict) and "description" in account:
        sent["account"] = account
        body["account"] = {k: v for k, v in account.items() if k != "description"}
    method = body.get("paymentMethod")
    if isinstance(method, dict):
        sent["paymentMethod"] = method
        body["paymentMethod"] = {k: v for k, v in method.items() if k in METHOD_KEYS}
    values, problems = specschema.check(
        "TMF676", schema, {name: value for name, value in sent.items() if value is not None}
    )
    stored = {name: value for name, value in values.items() if name != "account"}
    if "account" in values:
        stored["account"] = {"description": values["account"].get("description")}
    return stored, problems


def documents_body(row: Row, documents: tuple[str, ...]) -> dict[str, Any]:
    details = row.get("details") or {}
    return {name: details[name] for name in documents if name in details}


def account_extras(row: Row) -> dict[str, Any]:
    return (row.get("details") or {}).get("account", {})


def method_body(row: Row, name: str | None, at_type: str | None) -> dict[str, Any]:
    """The paymentMethod as sent (stored since 0028), else its name and @type."""
    return (row.get("details") or {}).get("paymentMethod") or without_nulls(
        {"name": name, "@type": at_type}
    )


def tax_part(total: Money, amount: Money | None, tax: Money | None, prefix: str = "") -> Decimal:
    """TMF676: `amount` is net of taxes, `taxAmount` the tax, `totalAmount` both. Given
    the total, the tax is `taxAmount`, else total - `amount`, else 0; with both sent,
    amount + taxAmount must be the total. All in one currency, no part negative."""
    for name, part in (("amount", amount), ("taxAmount", tax)):
        if part is not None and part.unit != total.unit:
            raise ValueError(f"{prefix}{name}.unit must be the totalAmount's {total.unit}")
    if amount is not None and tax is not None and amount.value + tax.value != total.value:
        raise ValueError(f"{prefix}amount + {prefix}taxAmount must equal {prefix}totalAmount")
    value = tax.value if tax is not None else total.value - amount.value if amount else Decimal(0)
    if not Decimal(0) <= value <= total.value:
        raise ValueError(f"{prefix}taxAmount must be from 0 to {prefix}totalAmount")
    return value


class PaymentItem(TmfModel):
    """Explicit lettering: this much of the payment pays that bill - amount and taxAmount
    together (bills are paid with their taxes)."""

    item: CustomerBillRef
    amount: Money
    total_amount: Money | None = None
    tax_amount: Money | None = None

    @model_validator(mode="after")
    def _shape(self) -> "PaymentItem":
        if self.amount.value <= 0:
            raise ValueError("paymentItem.amount.value must be positive")
        tax = self.tax_amount or Money(unit=self.amount.unit, value=Decimal(0))
        total = Money(unit=self.amount.unit, value=self.amount.value + tax.value)
        if self.total_amount is not None and self.total_amount != total:
            raise ValueError("paymentItem.totalAmount must equal its amount + taxAmount")
        tax_part(total, self.amount, tax, "paymentItem.")
        return self

    @property
    def allocated(self) -> Decimal:
        return self.amount.value + (self.tax_amount.value if self.tax_amount else Decimal(0))


class PaymentMethod(TmfModel):
    """How the money arrived: the method's name and @type, each kept as sent."""

    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _identified(self) -> "PaymentMethod":
        if not (self.name or self.at_type):
            raise ValueError("paymentMethod needs a name or an @type")
        return self


class PaymentCreate(TmfModel):
    account: BillingAccountRef
    total_amount: Money
    payment_method: PaymentMethod
    amount: Money | None = None
    tax_amount: Money | None = None
    payment_item: list[PaymentItem] = []

    @model_validator(mode="after")
    def _amounts(self) -> "PaymentCreate":
        if self.total_amount.value <= 0:
            raise ValueError("totalAmount.value must be positive")
        tax_part(self.total_amount, self.amount, self.tax_amount)
        return self

    @property
    def tax(self) -> Decimal:
        return tax_part(self.total_amount, self.amount, self.tax_amount)


def payment_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    total = money(row["amount"], row["currency"])
    tax = row.get("tax_amount", Decimal("0.00"))
    return {
        "id": str(row["id"]),
        "href": f"{BASE}/payment/{row['id']}",
        "paymentDate": utc(row["received_at"]),
        "status": row["status"],
        "statusDate": utc(row["received_at"]),
        "account": {
            "id": str(account["id"]),
            "href": account_href(account["id"]),
            "name": account["name"],
            **account_extras(row),
            "@referredType": "BillingAccount",
        },
        "amount": money(row["amount"] - tax, row["currency"]),
        "taxAmount": money(tax, row["currency"]),
        "totalAmount": total,
        "paymentMethod": method_body(row, row["payment_method_name"], row["payment_method_type"]),
        **documents_body(row, DOCUMENTS),
        **_items(conn, row),
        "@type": "Payment",
        **meta_body(row),
    }


def _items(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    """paymentItem[]: what the payment has paid so far, per bill (absent while nothing)."""
    items = [
        {
            "item": {
                "id": str(i["customer_bill_id"]),
                "href": bill_href(i["customer_bill_id"]),
                "@referredType": "CustomerBill",
            },
            "amount": money(i["amount"], row["currency"]),
            "totalAmount": money(i["amount"], row["currency"]),
            "@type": "PaymentItem",
        }
        for i in billing.payment_items(conn, row["id"])
    ]
    return {"paymentItem": items} if items else {}


def _settle(conn: psycopg.Connection, account_id: Any, row: Row, data: PaymentCreate, now) -> None:
    """Apply the new payment: its explicit items, then the oldest open bills. A refused
    item fails the whole POST - nothing is stored."""
    for item in data.payment_item:
        if item.amount.unit != row["currency"]:
            raise unprocessable(
                f"paymentItem amount for bill {item.item.id} is in {item.amount.unit}, not"
                f" in the account's currency {row['currency']}"
            )
    explicit = tuple(settlement.ExplicitItem(i.item.id, i.allocated) for i in data.payment_item)
    try:
        settle_account(
            conn,
            billing.load_account(conn, account_id),
            now=now,
            cause="payment",
            explicit=explicit,
            explicit_payment_id=row["id"] if explicit else None,
        )
    except (settlement.BillNotOpen, settlement.ExceedsRemaining) as exc:
        raise conflict(str(exc)) from None
    except settlement.SettlementError as exc:
        raise unprocessable(str(exc)) from None


@router.get("/payment")
def list_payments(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "account.id": ("billing_account_id", listing.as_uuid),
            "status": ("status", listing.as_str),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = payments.list_payments(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [payment_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/payment/{payment_id}")
def get_payment(payment_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = payments.get_payment(conn, parse_id("payment", payment_id))
        if row is None:
            raise not_found("payment", payment_id)
        body = payment_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


def post_payment(
    conn: psycopg.Connection,
    account: Row,
    data: PaymentCreate,
    documents: dict[str, Any],
    *,
    now,
    operator: str,
    record_topup: bool = True,
) -> tuple[Row, dict[str, Any]]:
    """Record one received payment under the account's row lock, settle the account and
    emit TMF676 Create. A prepaid account with a bucket also gets the completed top-up
    that records this payment (`record_topup`; the top-up API makes its own), so
    balanceActionHistory is complete whichever API took the money. The caller has checked
    the account and currency and holds `account` FOR UPDATE.
    """
    # the billing account's row lock first, as bill_account and PATCH customerBill take it:
    # a payment and a run (or two payments) never allocate the same credit
    billing.load_account(conn, account["id"])
    try:
        row = payments.insert_payment(
            conn,
            billing_account_id=account["id"],
            amount=data.total_amount.value,
            tax_amount=data.tax,
            currency=data.total_amount.unit,
            received_at=now,
            payment_method=data.payment_method.name or data.payment_method.at_type,
            method_name=data.payment_method.name,
            method_type=data.payment_method.at_type,
            details=documents,
        )
    except pg_errors.ForeignKeyViolation:
        # Backstop for the check by the caller: the composite FK (account, currency) is what
        # actually guarantees one currency per account.
        raise conflict("the payment's currency does not match the account's") from None
    row = with_meta(conn, "payment", row, data)
    _settle(conn, account["id"], row, data, now)
    new = payment_body(conn, row)
    emit.created(conn, "TMF676", "payment", new, at=now)
    if record_topup and account["rating_type"] == "prepaid":
        # imported here: app.api.topup imports this module
        from app.api import topup

        topup.record_payment(conn, row, new, operator=operator, now=now)
    return row, new


@router.post("/payment")
def create_payment(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    body_sent = copy.deepcopy(body)  # the Idempotency-Key hash covers what the client sent
    check_type(body, "Payment")
    for name in ("status", "statusDate", "paymentDate"):
        if name in body:
            raise bad_request(f"{name} is read-only")
    documents, found = take_documents(body, DOCUMENTS, "Payment_Create")
    data, _ = parse_documented(PaymentCreate, body, (), "TMF676", "Payment_Create", found)
    key = idempotency.header(request)
    with tx() as conn:
        if key is not None:
            digest = idempotency.request_hash(list(request.query_params.multi_items()), body_sent)
            replay = idempotency.begin(
                conn, request.state.operator, "POST", request.url.path, key, digest
            )
            if replay is not None:
                return idempotency.replayed(replay)
        # FOR UPDATE, the lock load_account takes in post_payment: a concurrent delete of the
        # account went first (422) or waits for this payment
        account = parties.get_billing_account(conn, data.account.id, "update")
        if account is None:
            raise unprocessable(f"account {data.account.id} does not exist")
        if account["currency"] != data.total_amount.unit:
            raise unprocessable(
                f"totalAmount.unit {data.total_amount.unit} does not match the account's"
                f" currency {account['currency']}"
            )
        now = clock.now()
        _, new = post_payment(
            conn, account, data, documents, now=now, operator=request.state.operator
        )
        selected = listing.select_fields(new, fields)
        if key is not None:
            idempotency.store(
                conn, request.state.operator, "POST", request.url.path, key, digest,
                status_code=201, response=selected, location=new["href"], now=now,
            )  # fmt: skip
    return created(selected)
