"""TMF676 completed payouts, serialized with settlement and billing."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from pydantic import model_validator

from app.api import listing
from app.api.common import (
    BillingAccountRef,
    Money,
    Ref,
    TmfModel,
    check_server_set,
    check_type,
    created,
    meta_body,
    money,
    parse,
    parse_id,
    take_server_set,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.payment import BASE, PaymentMethod, tax_part
from app.api.tmfjson import TmfJSONResponse
from app.billing.adapter import settle_account
from app.events import emit
from app.repository import billing, parties, refunds

router = APIRouter(prefix=BASE)
ATTRIBUTES = (
    "account",
    "payment",
    "amount",
    "totalAmount",
    "taxAmount",
    "refundDate",
    "statusDate",
    "status",
    "paymentMethod",
    "description",
)


class PaymentRef(Ref):
    referred_type = "Payment"


class RefundCreate(TmfModel):
    account: BillingAccountRef
    total_amount: Money
    payment_method: PaymentMethod
    amount: Money | None = None
    tax_amount: Money | None = None
    payment: PaymentRef | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _amounts(self) -> "RefundCreate":
        if self.total_amount.value <= 0:
            raise ValueError("totalAmount.value must be positive")
        tax_part(self.total_amount, self.amount, self.tax_amount)
        return self

    @property
    def tax(self) -> Decimal:
        return tax_part(self.total_amount, self.amount, self.tax_amount)


def refund_body(row: dict) -> dict[str, Any]:
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/refund/{row['id']}",
            "@type": "Refund",
            "account": {
                "id": str(row["billing_account_id"]),
                "href": f"/tmf-api/accountManagement/v5/billingAccount/{row['billing_account_id']}",
                "@referredType": "BillingAccount",
            },
            "payment": {
                "id": str(row["payment_id"]),
                "href": f"{BASE}/payment/{row['payment_id']}",
                "@referredType": "Payment",
            }
            if row["payment_id"]
            else None,
            "totalAmount": money(row["amount"], row["currency"]),
            "amount": money(row["amount"] - row.get("tax_amount", 0), row["currency"]),
            "taxAmount": money(row.get("tax_amount", Decimal("0.00")), row["currency"]),
            "refundDate": utc(row["refunded_at"]),
            "statusDate": utc(row["refunded_at"]),
            "status": "done",
            "description": row["description"],
            "paymentMethod": without_nulls(
                {"name": row["method_name"], "@type": row["method_type"]}
            ),
        }
    ) | meta_body(row)


@router.get("/refund")
def list_refunds(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "account.id": ("billing_account_id", listing.as_uuid),
            "status": ("status", listing.as_str),
            "payment.id": ("payment_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = refunds.page(conn, where=query.where, offset=query.offset, limit=query.limit)
    return listing.list_response([refund_body(r) for r in rows], total, query)


@router.get("/refund/{refund_id}")
def get_refund(refund_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = refunds.get(conn, parse_id("refund", refund_id))
        if row is None:
            raise not_found("refund", refund_id)
    return TmfJSONResponse(listing.select_fields(refund_body(row), fields))


@router.post("/refund")
def create_refund(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "Refund")
    server_set = take_server_set(body, ("status", "statusDate", "refundDate"))
    data = parse(RefundCreate, body)
    with tx() as conn:
        # FOR UPDATE, as load_account below: a concurrent account delete went first or waits
        account_row = parties.get_billing_account(conn, data.account.id, "update")
        if account_row is None:
            raise unprocessable("account does not exist")
        account = billing.load_account(conn, data.account.id)
        if data.total_amount.unit != account.currency:
            raise unprocessable("refund currency must match the account")
        now = clock.now()
        settle_account(conn, account, now=now, cause="payment")
        bills, payments = billing.load_settlement(conn, account.id)
        sources = [
            (p.id, None, p.left)
            for p in payments
            if data.payment is None or p.id == data.payment.id
        ]
        if data.payment is not None and not any(p.id == data.payment.id for p in payments):
            raise unprocessable("payment is not a payment of this account")
        if data.payment is None:
            sources += [
                (None, b.id, b.credit_left)
                for b in sorted(bills, key=lambda b: (b.bill_date, b.id))
            ]
        available = min(
            sum((left for _, _, left in sources), Decimal(0)),
            max(billing.account_balance(conn, account.id), Decimal(0)),
        )
        if data.total_amount.value > available:
            raise conflict(f"refund exceeds refundable credit {available}")
        row = refunds.insert(conn, account.id, data, now)
        left = data.total_amount.value
        for payment_id, credit_bill_id, credit in sources:
            take = min(left, credit)
            if take > 0:
                refunds.consume(conn, row, payment_id, credit_bill_id, take)
                left -= take
            if left == 0:
                break
        conn.execute("SET CONSTRAINTS credit_usage_bounds IMMEDIATE")
        new = refund_body(with_meta(conn, "refund", row, data))
        check_server_set(server_set, new)
        emit.created(conn, "TMF676", "refund", new, at=now)
    return created(listing.select_fields(new, fields))
