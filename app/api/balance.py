"""TMF654 v4 prepaid balances: bucket and accumulatedBalance reads. docs/decisions/prepaid.md.

A bucket is a view of the prepaid account's funding ledger, one per account and
currency: `remainingValue` is spendable L - H (unclamped, so a debt shows), and
`reservedValue` is the outstanding holds H. The account's TMF666 `currentBalance` is L.
The accumulatedBalance totals the account's buckets (one today). Balance actions
(top-up, adjustment, reservation, transfer, history) follow in later slices.
"""

from typing import Any

from fastapi import APIRouter, Request

from app.api import listing
from app.api.account import account_href
from app.api.common import CENT, parse_id, ref, utc, without_nulls
from app.api.customer import customer_href
from app.api.deps import ClockDep, Tx
from app.api.errors import not_found
from app.api.inventory import product_href
from app.api.resource_inventory import BASE as RESOURCE_BASE
from app.api.tmfjson import TmfJSONResponse
from app.repository import buckets as repo
from app.repository._sql import Row

API = "TMF654"
BASE = "/tmf-api/prepayBalanceManagement/v4"
router = APIRouter(prefix=BASE)
# requestedDate and confirmationDate date a deduction: a bucket is never one.
BUCKET_ATTRIBUTES = (
    "name",
    "description",
    "isShared",
    "remainingValueName",
    "logicalResource",
    "partyAccount",
    "product",
    "relatedParty",
    "remainingValue",
    "reservedValue",
    "status",
    "usageType",
    "validFor",
)
ACCUMULATED_ATTRIBUTES = (
    "name",
    "description",
    "bucket",
    "logicalResource",
    "partyAccount",
    "product",
    "relatedParty",
    "totalBalance",
)


def quantity(amount, units: str) -> dict[str, Any]:
    return {"amount": amount.quantize(CENT), "units": units}


def _owner(conn, row: Row, today) -> dict[str, Any]:
    """What a bucket and its accumulatedBalance share: figures, owner, products, numbers."""
    figures = repo.figures(conn, row["billing_account_id"])
    products = repo.live_products(conn, row["billing_account_id"], today)
    numbers = repo.numbers(conn, [p["id"] for p in products], today)
    return {
        "remaining": figures["ledger"] - figures["reserved"],
        "reserved": figures["reserved"],
        "numbers": [
            ref(
                "LogicalResource",
                f"{RESOURCE_BASE}/resource/{n['id']}",
                n["id"],
                n["name"],
                ref_type="LogicalResourceRef",
            )
            for n in sorted(numbers, key=lambda n: n["name"])
        ],
        "partyAccount": ref(
            "BillingAccount",
            account_href(row["billing_account_id"]),
            row["billing_account_id"],
            row["account_name"],
            ref_type="PartyAccountRef",
        )
        | without_nulls(
            {"description": row["account_description"], "status": row["account_state"]}
        ),
        "product": [
            ref("Product", product_href(p["id"]), p["id"], p["name"], ref_type="ProductRef")
            for p in products
        ],
        "relatedParty": [
            ref(
                "Customer",
                customer_href(row["customer_id"]),
                row["customer_id"],
                row["customer_name"],
                ref_type="RelatedParty",
            )
            | {"role": "customer"}
        ],
    }


def bucket_href(bucket_id: Any) -> str:
    return f"{BASE}/bucket/{bucket_id}"


def bucket_body(conn, row: Row, today) -> dict[str, Any]:
    owner = _owner(conn, row, today)
    currency = row["currency"]
    remaining = quantity(owner["remaining"], currency)
    return {
        "id": str(row["id"]),
        "href": bucket_href(row["id"]),
        "@type": "Bucket",
        "name": f"{row['account_name']} {currency}",
        "description": f"Prepaid {currency} balance of billing account {row['account_name']}",
        "usageType": row["usage_type"],
        "status": row["status"],
        "isShared": len(owner["numbers"]) > 1,
        "remainingValue": remaining,
        "remainingValueName": f"{remaining['amount']} {currency}",
        "reservedValue": quantity(owner["reserved"], currency),
        "validFor": {"startDateTime": utc(row["created_at"])},
        "partyAccount": owner["partyAccount"],
        "relatedParty": owner["relatedParty"],
        "product": owner["product"],
        "logicalResource": owner["numbers"],
    }


def accumulated_body(conn, row: Row, today) -> dict[str, Any]:
    owner = _owner(conn, row, today)
    currency = row["currency"]
    body = {
        "id": str(row["accumulated_balance_id"]),
        "href": f"{BASE}/accumulatedBalance/{row['accumulated_balance_id']}",
        "@type": "AccumulatedBalance",
        "name": f"{row['account_name']} {currency}",
        "description": f"Total prepaid {currency} balance of billing account {row['account_name']}",
        "bucket": [
            ref(
                "Bucket",
                bucket_href(row["id"]),
                row["id"],
                f"{row['account_name']} {currency}",
                ref_type="BucketRef",
            )
        ],
        "totalBalance": quantity(owner["remaining"], currency),
        "partyAccount": owner["partyAccount"],
        "relatedParty": owner["relatedParty"],
        "product": owner["product"],
    }
    # A single reference in v4: only when the buckets fund exactly one number.
    if len(owner["numbers"]) == 1:
        body["logicalResource"] = owner["numbers"][0]
    return body


FILTERS = {
    "id": ("id", listing.as_uuid),
    "status": ("status", listing.as_str),
    "usageType": ("usage_type", listing.as_str),
    "partyAccount.id": ("billing_account_id", listing.as_uuid),
}


@router.get("/bucket")
def list_buckets(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=FILTERS, attributes=BUCKET_ATTRIBUTES)
    today = clock.today()
    with tx() as conn:
        rows, total = repo.page(conn, query.where, query.offset, query.limit)
        bodies = [bucket_body(conn, row, today) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get("/bucket/{bucket_id}")
def get_bucket(bucket_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, BUCKET_ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, "id", parse_id("bucket", bucket_id))
        if row is None:
            raise not_found("bucket", bucket_id)
        body = bucket_body(conn, row, clock.today())
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.get("/accumulatedBalance")
def list_accumulated_balances(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("accumulated_balance_id", listing.as_uuid),
            "partyAccount.id": ("billing_account_id", listing.as_uuid),
        },
        attributes=ACCUMULATED_ATTRIBUTES,
    )
    today = clock.today()
    with tx() as conn:
        rows, total = repo.page(conn, query.where, query.offset, query.limit)
        bodies = [accumulated_body(conn, row, today) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get("/accumulatedBalance/{balance_id}")
def get_accumulated_balance(
    balance_id: str, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ACCUMULATED_ATTRIBUTES)
    with tx() as conn:
        uid = parse_id("accumulatedBalance", balance_id)
        row = repo.get(conn, "accumulated_balance_id", uid)
        if row is None:
            raise not_found("accumulatedBalance", balance_id)
        body = accumulated_body(conn, row, clock.today())
    return TmfJSONResponse(listing.select_fields(body, fields))
