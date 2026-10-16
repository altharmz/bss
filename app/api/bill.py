"""TMF678 Customer Bill Management v5: customerBill and appliedCustomerBillingRate.

Only the billing run creates bills and ABRs (stage 3/4), so both lists are empty until
then. `charge_type` maps to the spec's appliedBillingRateType: recurring ->
appliedBillingCharge, refund -> appliedBillingCredit.

A bill's state moves with its settlement (docs/bill-lifecycle-design.md, section 6):
the run issues it (new -> validated -> sent), allocations make it partiallyPaid or
settled, and PATCH lets the operator put it on hold or release it - nothing else.
remainingAmount and appliedPayment come from the allocations.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import account_ref
from app.api.common import (
    TmfModel,
    check_type,
    date_period,
    from_billing_date,
    money,
    operator_of,
    parse,
    parse_id,
    ref,
    to_billing_date,
    utc,
    without_nulls,
)
from app.api.deps import ClockDep, MergePatchBody, Tx
from app.api.errors import conflict, not_found
from app.api.inventory import product_href
from app.api.tmfjson import TmfJSONResponse
from app.billing import settlement
from app.billing.adapter import settle_account
from app.repository import billing, catalog, parties, products
from app.repository._sql import IS_NULL, NOT_NULL, Row, select_page

BASE = "/tmf-api/customerBillManagement/v5"
router = APIRouter(prefix=BASE)

# TMF676 lives in app/api/payment.py, which imports this module for its paymentItem.
PAYMENT_BASE = "/tmf-api/paymentManagement/v4"

RATE_TYPE = {"recurring": "appliedBillingCharge", "refund": "appliedBillingCredit"}

BILL_ATTRIBUTES = (
    "billDate",
    "billingPeriod",
    "amountDue",
    "remainingAmount",
    "paymentDueDate",
    "appliedPayment",
    "billNo",
    "runType",
    "category",
    "state",
    "billingAccount",
    "lastUpdate",
    "billCycle",
    "nextBillDate",
    "taxExcludedAmount",
    "taxIncludedAmount",
    "billDocument",
)
# Every other attribute of the spec's CustomerBill: present, but not changed by a PATCH.
BILL_READ_ONLY = (
    *(a for a in BILL_ATTRIBUTES if a != "state"),
    "billCycle",
    "billDocument",
    "financialAccount",
    "nextBillDate",
    "paymentMethod",
    "relatedParty",
    "taxExcludedAmount",
    "taxIncludedAmount",
    "taxItem",
)

BillState = Literal["new", "onHold", "validated", "sent", "settled", "partiallyPaid"]


class BillPatch(TmfModel):
    state: BillState


RATE_ATTRIBUTES = (
    "date",
    "appliedBillingRateType",
    "taxExcludedAmount",
    "periodCoverage",
    "isBilled",
    "bill",
    "billingAccount",
    "product",
)


def bill_href(bill_id: Any) -> str:
    return f"{BASE}/customerBill/{bill_id}"


def _account_ref(conn: psycopg.Connection, account_id: Any) -> dict[str, Any]:
    account = parties.get_billing_account(conn, account_id)
    assert account is not None  # foreign key
    return account_ref(account)


def payment_href(payment_id: Any) -> str:
    return f"{PAYMENT_BASE}/payment/{payment_id}"


def bill_no(bill_id: Any) -> str:
    """The display number, like BA-xxxxxxxx for accounts: the first 8 hex digits."""
    return "BL-" + str(bill_id).replace("-", "")[:8].upper()


def bill_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    currency = row["currency"]
    allocated = billing.bill_allocated(conn, row["id"])
    return {
        "id": str(row["id"]),
        "href": bill_href(row["id"]),
        "billNo": bill_no(row["id"]),
        "billDate": from_billing_date(row["bill_date"]),
        "billingPeriod": date_period(row["bill_period"]),
        "paymentDueDate": from_billing_date(row["payment_due_date"]),
        "amountDue": money(row["amount_due"], currency),
        "taxExcludedAmount": money(row["amount_due"], currency),
        "taxIncludedAmount": money(row["amount_due"], currency),
        "billCycle": ref("BillCycle", cycle_href(row["billing_run_id"]), row["billing_run_id"]),
        # the bill's own period ends on the next cycle day, whatever the account says today
        "nextBillDate": from_billing_date(row["bill_period"].upper),
        "remainingAmount": money(billing.remaining_amount(row, allocated), currency),
        "appliedPayment": [
            {
                "appliedAmount": money(a["amount"], currency),
                "payment": ref("Payment", payment_href(a["payment_id"]), a["payment_id"]),
            }
            for a in billing.applied_payments(conn, row["id"])
        ],
        "category": row["category"],
        "runType": row["run_type"],
        "billDocument": [
            {
                "@type": "Attachment",
                "name": f"{bill_no(row['id'])}.json",
                "attachmentType": "bill",
                "mimeType": "application/json",
                "content": row["bill_document"],
            }
        ]
        if row["bill_document"]
        else [],
        "state": row["state"],
        "billingAccount": _account_ref(conn, row["billing_account_id"]),
        "lastUpdate": utc(row["updated_at"]),
        "@type": "CustomerBill",
    }


def rate_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    product = products.get_product(conn, row["product_id"])
    assert product is not None  # foreign key
    offering = catalog.get_product_offering(conn, product["product_offering_id"])
    assert offering is not None  # foreign key
    bill_id = row["customer_bill_id"]
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/appliedCustomerBillingRate/{row['id']}",
            "date": utc(row["applied_at"]),
            "appliedBillingRateType": RATE_TYPE[row["charge_type"]],
            "taxExcludedAmount": money(row["amount"], row["currency"]),
            "periodCoverage": date_period(row["period"]),
            "isBilled": bill_id is not None,
            "bill": ref("CustomerBill", bill_href(bill_id), bill_id) if bill_id else None,
            "billingAccount": _account_ref(conn, row["billing_account_id"]),
            "product": ref("Product", product_href(product["id"]), product["id"], offering["name"]),
            "@type": "AppliedCustomerBillingRate",
        }
    )


def _is_billed(raw: str) -> object:
    return NOT_NULL if listing.as_bool(raw) else IS_NULL


@router.get("/customerBill")
def list_bills(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
            "state": ("state", listing.as_str),
        },
        attributes=BILL_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = billing.page_bills(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [bill_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/customerBill/{bill_id}")
def get_bill(bill_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, BILL_ATTRIBUTES)
    with tx() as conn:
        row = billing.get_bill(conn, parse_id("customerBill", bill_id))
        if row is None:
            raise not_found("customerBill", bill_id)
        body = bill_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.patch("/customerBill/{bill_id}")
def patch_bill(
    bill_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Hold or release a bill (design 6.2): `onHold` from sent or partiallyPaid; from
    onHold only the state its allocations imply (sent if nothing was paid, else
    partiallyPaid). Repeating the current state is a 200 no-op; anything else is a 409
    with the reason. A release settles the account: credit may have waited for the bill."""
    fields = listing.retrieve_fields(request, BILL_ATTRIBUTES)
    check_type(body, "CustomerBill")
    data = parse(BillPatch, body, read_only=BILL_READ_ONLY)
    with tx() as conn:
        uid = parse_id("customerBill", bill_id)
        row = billing.get_bill(conn, uid)
        if row is None:
            raise not_found("customerBill", bill_id)
        account = billing.load_account(conn, row["billing_account_id"])  # lock order
        row = billing.lock_bill(conn, uid)
        assert row is not None
        current = settlement.Bill(
            id=row["id"],
            bill_date=row["bill_date"],
            amount_due=row["amount_due"],
            state=row["state"],
            allocated=billing.bill_allocated(conn, uid),
        )
        try:
            change = settlement.operator_change(current, data.state)
        except settlement.TransitionRefused as exc:
            raise conflict(str(exc)) from None
        if change is not None:
            now = clock.now()
            billing.write_settlement(
                conn,
                account,
                settlement.Settlement((), (change,)),
                now=now,
                cause="operator",
                operator=operator_of(request),
            )
            if change.from_state == "onHold":
                settle_account(conn, account, now=now, cause="payment")
        patched = bill_body(conn, billing.get_bill(conn, uid))
    return TmfJSONResponse(listing.select_fields(patched, fields))


CYCLE_ATTRIBUTES = ("name", "billingDate", "billingPeriod", "description", "validFor")


def cycle_href(cycle_id: Any) -> str:
    return f"{BASE}/billCycle/{cycle_id}"


def cycle_body(row: Row) -> dict[str, Any]:
    day = row["run_date"]
    return {
        "id": str(row["id"]),
        "href": cycle_href(row["id"]),
        "@type": "BillCycle",
        "name": f"Daily billing {day}",
        "billingDate": from_billing_date(day),
        "billingPeriod": day.isoformat(),
        "description": (
            "Daily billing run; account billing periods are carried by individual bills."
        ),
        "validFor": {
            "startDateTime": from_billing_date(day),
            "endDateTime": from_billing_date(day + timedelta(days=1)),
        },
    }


def _billing_date(raw: str):
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise ValueError("billingDate needs an offset")
    return to_billing_date(value)


@router.get("/billCycle")
def list_bill_cycles(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={"billingDate": ("run_date", _billing_date)},
        attributes=CYCLE_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = select_page(
            conn, "billing_run", where=query.where, offset=query.offset, limit=query.limit
        )
    return listing.list_response([cycle_body(row) for row in rows], total, query)


@router.get("/billCycle/{cycle_id}")
def get_bill_cycle(cycle_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    with tx() as conn:
        row = billing.get_run_by_id(conn, parse_id("billCycle", cycle_id))
        if row is None:
            raise not_found("billCycle", cycle_id)
    return TmfJSONResponse(listing.select_fields(cycle_body(row), fields))


@router.get("/appliedCustomerBillingRate")
def list_rates(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
            "product.id": ("product_id", listing.as_uuid),
            "bill.id": ("customer_bill_id", listing.as_uuid),
            "isBilled": ("customer_bill_id", _is_billed),
        },
        attributes=RATE_ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = billing.page_abrs(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [rate_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/appliedCustomerBillingRate/{rate_id}")
def get_rate(rate_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, RATE_ATTRIBUTES)
    with tx() as conn:
        row = billing.get_abr(conn, parse_id("appliedCustomerBillingRate", rate_id))
        if row is None:
            raise not_found("appliedCustomerBillingRate", rate_id)
        body = rate_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))
