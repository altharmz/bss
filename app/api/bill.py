"""TMF678 Customer Bill Management v5: customerBill and appliedCustomerBillingRate.

Only the billing run creates bills and ABRs (stage 3/4), so both lists are empty until
then. `charge_type` maps to the spec's appliedBillingRateType: recurring ->
appliedBillingCharge, refund -> appliedBillingCredit.

A bill's state moves with its settlement (docs/archive/bill-lifecycle-design.md, section 6):
the run issues it (new -> validated -> sent), allocations make it partiallyPaid or
settled, and PATCH lets the operator put it on hold or release it - nothing else.
remainingAmount and appliedPayment come from the allocations.
"""

import base64
import json
from datetime import datetime, timedelta
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import (
    CYCLE_BASE,
    CYCLE_ID,
    account_ref,
    customer_party,
    cycle_specification_body,
    linked_financial,
)
from app.api.accounts import financial_ref
from app.api.common import (
    TmfModel,
    check_server_set,
    check_type,
    date_period,
    from_billing_date,
    money,
    not_null,
    operator_of,
    parse,
    parse_id,
    ref,
    take_server_set,
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
from app.config import settings
from app.repository import billing, catalog, parties
from app.repository._sql import IS_NULL, NOT_NULL, Row, select_page

BASE = "/tmf-api/customerBillManagement/v5"
router = APIRouter(prefix=BASE)

# TMF676 lives in app/api/payment.py, which imports this module for its paymentItem.
PAYMENT_BASE = "/tmf-api/payment/v4"

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
    "taxItem",
    "billDocument",
    "financialAccount",
    "paymentMethod",
    "relatedParty",
)
# Every other attribute of the spec's CustomerBill: present, but not changed by a PATCH.
BILL_READ_ONLY = tuple(a for a in BILL_ATTRIBUTES if a != "state")

BillState = Literal["new", "onHold", "validated", "sent", "settled", "partiallyPaid"]


class BillPatch(TmfModel):
    state: BillState | None = None


RATE_ATTRIBUTES = (
    "name",
    "description",
    "characteristic",
    "date",
    "appliedBillingRateType",
    "taxExcludedAmount",
    "taxIncludedAmount",
    "appliedTax",
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
    account = parties.get_billing_account(conn, row["billing_account_id"])
    assert account is not None  # foreign key
    financial = linked_financial(conn, account)
    allocated = billing.bill_allocated(conn, row["id"])
    net, tax_lines = billing.bill_taxes(conn, row["id"])
    return {
        "id": str(row["id"]),
        "href": bill_href(row["id"]),
        "billNo": bill_no(row["id"]),
        "billDate": from_billing_date(row["bill_date"]),
        "billingPeriod": date_period(row["bill_period"]),
        "paymentDueDate": from_billing_date(row["payment_due_date"]),
        "amountDue": money(row["amount_due"], currency),
        "taxExcludedAmount": money(net, currency),
        "taxIncludedAmount": money(row["amount_due"], currency),
        "taxItem": [
            {
                "taxCategory": t["category"],
                "taxRate": t["rate"],
                "taxAmount": money(t["amount"], currency),
                "@type": "TaxItem",
            }
            for t in tax_lines
        ],
        "billCycle": cycle_ref(conn, row["billing_run_id"]),
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
        "billDocument": [_document_body(row)] if row["bill_document"] else [],
        "state": row["state"],
        "billingAccount": account_ref(account),
        **({} if financial is None else {"financialAccount": financial_ref(financial)}),
        **(
            {"paymentMethod": account["details"]["defaultPaymentMethod"]}
            if "defaultPaymentMethod" in account["details"]
            else {}
        ),
        "relatedParty": [customer_party(conn, account)],
        "lastUpdate": utc(row["updated_at"]),
        "@type": "CustomerBill",
    }


def _document_body(row: Row) -> dict[str, Any]:
    """The bill's one document, embedded (`content`): it has no address of its own (no
    href or url) and no validity period."""
    raw = base64.b64decode(row["bill_document"])
    fmt = json.loads(raw).get("format", {}).get("name", "JSON detailed")
    return {
        "@type": "Attachment",
        "id": str(row["id"]),
        "name": f"{bill_no(row['id'])}.json",
        "description": f"Bill {bill_no(row['id'])}, {fmt}",
        "attachmentType": "bill",
        "mimeType": "application/json",
        "content": row["bill_document"],
        "size": {"amount": len(raw), "units": "bytes"},
    }


def rate_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    offering = catalog.get_product_offering(conn, row["product_offering_id"])
    assert offering is not None  # foreign key
    bill_id = row["customer_bill_id"]
    period = row["period"]
    covered = f"{offering['name']}, {period.lower} - {period.upper - timedelta(days=1)}"
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{BASE}/appliedCustomerBillingRate/{row['id']}",
            "name": offering["name"],
            "description": f"refund of {covered}" if row["charge_type"] == "refund" else covered,
            "characteristic": [],  # billing sets none
            "date": utc(row["applied_at"]),
            "appliedBillingRateType": RATE_TYPE[row["charge_type"]],
            "taxExcludedAmount": money(row["amount"], row["currency"]),
            "taxIncludedAmount": money(billing.abr_total(row), row["currency"]),
            "appliedTax": [
                {
                    "taxCategory": t.category,
                    "taxRate": t.rate,
                    "taxAmount": money(t.amount, row["currency"]),
                    "@type": "AppliedBillingTaxRate",
                }
                for t in billing.applied_taxes(row)
            ],
            "periodCoverage": date_period(row["period"]),
            "isBilled": bill_id is not None,
            "bill": ref("CustomerBill", bill_href(bill_id), bill_id, bill_no(bill_id))
            if bill_id
            else None,
            "billingAccount": _account_ref(conn, row["billing_account_id"]),
            "product": ref(
                "Product", product_href(row["product_id"]), row["product_id"], offering["name"]
            ),
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
    # every attribute but state is the billing run's: repeated, never changed
    server_set = take_server_set(body, BILL_READ_ONLY)
    data = parse(BillPatch, body)
    not_null(data, "state")
    with tx() as conn:
        uid = parse_id("customerBill", bill_id)
        row = billing.get_bill(conn, uid)
        if row is None:
            raise not_found("customerBill", bill_id)
        check_server_set(server_set, bill_body(conn, row))
        if data.state is None:
            return TmfJSONResponse(listing.select_fields(bill_body(conn, row), fields))
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


CYCLE_ATTRIBUTES = (
    "name",
    "billingDate",
    "billingPeriod",
    "description",
    "validFor",
    "chargeDate",
    "creditDate",
    "mailingDate",
    "paymentDueDate",
    "BillCycleSpecification",
)


def cycle_href(cycle_id: Any) -> str:
    return f"{BASE}/billCycle/{cycle_id}"


def _cycle_name(day: Any) -> str:
    return f"Daily billing {day}"


def cycle_ref(conn: psycopg.Connection, run_id: Any) -> dict[str, Any]:
    run = billing.get_run_by_id(conn, run_id)
    assert run is not None  # foreign key
    return ref("BillCycle", cycle_href(run_id), run_id, _cycle_name(run["run_date"]))


def cycle_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    """A run charges, issues its bills and applies credit on its own date; its bills are
    due `BILL_PAYMENT_TERM_DAYS` later (the term they were issued with). Every run applies
    the one monthly policy, whatever an account's named specification calls it."""
    day = row["run_date"]
    on = from_billing_date(day)
    policy = cycle_specification_body()
    due = billing.run_payment_due_date(conn, row["id"]) or day + timedelta(
        days=settings.bill_payment_term_days
    )
    return {
        "id": str(row["id"]),
        "href": cycle_href(row["id"]),
        "@type": "BillCycle",
        "name": _cycle_name(day),
        "billingDate": on,
        "billingPeriod": day.isoformat(),
        "chargeDate": on,
        "creditDate": on,
        "mailingDate": on,
        "paymentDueDate": from_billing_date(due),
        "BillCycleSpecification": {
            **ref(
                "BillingCycleSpecification",
                f"{CYCLE_BASE}/{CYCLE_ID}",
                CYCLE_ID,
                policy["name"],
                ref_type="BillCycleSpecificationRef",
            ),
            "description": policy["description"],
        },
        "description": (
            "Daily billing run; account billing periods are carried by individual bills."
        ),
        "validFor": {
            "startDateTime": on,
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
        items = [cycle_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/billCycle/{cycle_id}")
def get_bill_cycle(cycle_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    with tx() as conn:
        row = billing.get_run_by_id(conn, parse_id("billCycle", cycle_id))
        if row is None:
            raise not_found("billCycle", cycle_id)
        body = cycle_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


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
