"""TMF678 Customer Bill Management v5: customerBill and appliedCustomerBillingRate.

Read-only. Only the billing run writes these tables (stage 3/4), so both lists are empty
until then. `charge_type` maps to the spec's appliedBillingRateType: recurring ->
appliedBillingCharge, refund -> appliedBillingCredit.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import account_ref
from app.api.common import date_period, from_billing_date, money, parse_id, ref, utc, without_nulls
from app.api.deps import Tx
from app.api.errors import not_found
from app.api.inventory import product_href
from app.api.tmfjson import TmfJSONResponse
from app.repository import billing, catalog, parties, products
from app.repository._sql import IS_NULL, NOT_NULL, Row

BASE = "/tmf-api/customerBillManagement/v5"
router = APIRouter(prefix=BASE)

RATE_TYPE = {"recurring": "appliedBillingCharge", "refund": "appliedBillingCredit"}

BILL_ATTRIBUTES = (
    "billDate",
    "billingPeriod",
    "amountDue",
    "state",
    "billingAccount",
    "lastUpdate",
)
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


def bill_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "href": bill_href(row["id"]),
        "billDate": from_billing_date(row["bill_date"]),
        "billingPeriod": date_period(row["bill_period"]),
        "amountDue": money(row["amount_due"], row["currency"]),
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
            "date": utc(row["created_at"]),
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


@router.get("/appliedCustomerBillingRate")
def list_rates(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "billingAccount.id": ("billing_account_id", listing.as_uuid),
            "product.id": ("product_id", listing.as_uuid),
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
