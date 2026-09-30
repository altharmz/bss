"""TMF676 Payment Management v4: payment. (TMF676 has no v5.)

POST records a payment that has been received (`status: done`); there is no payment
processing here. Taxes are out of scope, so `amount` always equals `totalAmount`.
"""

from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Request
from psycopg import errors as pg_errors
from pydantic import Field, model_validator

from app.api import listing
from app.api.account import account_href
from app.api.common import Money, Ref, TmfModel, created, money, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.repository import parties, payments
from app.repository._sql import Row

BASE = "/tmf-api/paymentManagement/v4"
router = APIRouter(prefix=BASE)

ATTRIBUTES = (
    "account",
    "amount",
    "totalAmount",
    "paymentDate",
    "paymentMethod",
    "status",
    "statusDate",
)


class PaymentMethod(TmfModel):
    """We keep one word about how the money arrived: the method's name, or its @type."""

    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _identified(self) -> "PaymentMethod":
        if not (self.name or self.at_type):
            raise ValueError("paymentMethod needs a name or an @type")
        return self


class PaymentCreate(TmfModel):
    account: Ref
    total_amount: Money
    payment_method: PaymentMethod
    amount: Money | None = None
    tax_amount: Money | None = None

    @model_validator(mode="after")
    def _no_taxes(self) -> "PaymentCreate":
        if self.total_amount.value <= 0:
            raise ValueError("totalAmount.value must be positive")
        if self.amount is not None and self.amount != self.total_amount:
            raise ValueError("amount must equal totalAmount (taxes are out of scope)")
        if self.tax_amount is not None and self.tax_amount.value != Decimal(0):
            raise ValueError("taxAmount must be zero (taxes are out of scope)")
        return self


def payment_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    amount = money(row["amount"], row["currency"])
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
            "@referredType": "BillingAccount",
        },
        "amount": amount,
        "totalAmount": amount,
        "paymentMethod": {"name": row["payment_method"], "@type": row["payment_method"]},
        "@type": "Payment",
    }


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


@router.post("/payment")
def create_payment(body: JsonBody, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    data = parse(PaymentCreate, body, read_only=("status", "statusDate", "paymentDate"))
    with tx() as conn:
        account = parties.get_billing_account(conn, data.account.id)
        if account is None:
            raise unprocessable(f"account {data.account.id} does not exist")
        if account["currency"] != data.total_amount.unit:
            raise unprocessable(
                f"totalAmount.unit {data.total_amount.unit} does not match the account's"
                f" currency {account['currency']}"
            )
        try:
            row = payments.insert_payment(
                conn,
                billing_account_id=account["id"],
                amount=data.total_amount.value,
                currency=data.total_amount.unit,
                received_at=clock.now(),
                payment_method=data.payment_method.name or data.payment_method.at_type,
            )
        except pg_errors.ForeignKeyViolation:
            # Backstop for the check above: the composite FK (account, currency) is what
            # actually guarantees one currency per account.
            raise conflict("the payment's currency does not match the account's") from None
        new = payment_body(conn, row)
    return created(new)
