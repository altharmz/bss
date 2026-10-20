"""GET /internal/v1/customerBill/{id}: one bill as the admin UI's bill dialog shows it.

Everything TMF678 has, plus what it cannot carry: the charges on the bill, every source
that paid it (payments and credit notes, automatic or explicit), its state history, and
the states an operator may set now (`allowedStates`, from the same rule PATCH applies) -
so the UI never re-implements the hold/release rules (docs/archive/bill-lifecycle-design.md 6.5).
"""

from fastapi import APIRouter, Request

from app.api import bill as tmf_bill
from app.api.common import money, parse_id, utc
from app.api.deps import ClockDep, Tx
from app.api.errors import not_found
from app.api.inventory import product_href
from app.api.tmfjson import TmfJSONResponse
from app.billing import settlement
from app.internal.context import BASE, no_query
from app.repository import billing

router = APIRouter(prefix=BASE)


@router.get("/customerBill/{bill_id}")
def bill_detail(bill_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = billing.get_bill(conn, parse_id("customerBill", bill_id))
        if row is None:
            raise not_found("customerBill", bill_id)
        currency = row["currency"]
        allocated = billing.bill_allocated(conn, row["id"])
        remaining = billing.remaining_amount(row, allocated)
        current = settlement.Bill(
            id=row["id"],
            bill_date=row["bill_date"],
            amount_due=row["amount_due"],
            state=row["state"],
            allocated=allocated,
        )
        charges = [
            {
                "id": str(a["id"]),
                "href": f"{tmf_bill.BASE}/appliedCustomerBillingRate/{a['id']}",
                "kind": "charge" if a["charge_type"] == "recurring" else "refund",
                "period": {"start": a["period"].lower, "end": a["period"].upper},
                "amount": money(billing.abr_total(a), currency),
                "product": {"id": str(a["product_id"]), "href": product_href(a["product_id"])},
                "productOffering": {"id": str(a["offering_id"]), "name": a["offering_name"]},
            }
            for a in billing.bill_charges(conn, row["id"])
        ]
        sources = [
            {
                "kind": "payment" if s["payment_id"] else "creditNote",
                "id": str(s["payment_id"] or s["credit_bill_id"]),
                "href": tmf_bill.payment_href(s["payment_id"])
                if s["payment_id"]
                else tmf_bill.bill_href(s["credit_bill_id"]),
                "label": s["payment_method"]
                if s["payment_id"]
                else tmf_bill.bill_no(s["credit_bill_id"]),
                "amount": money(s["amount"], currency),
                "allocation": s["kind"],
                "allocatedAt": utc(s["allocated_at"]),
            }
            for s in billing.bill_sources(conn, row["id"])
        ]
        history = [
            {
                "fromState": h["from_state"],
                "toState": h["to_state"],
                "changedAt": utc(h["changed_at"]),
                "cause": h["cause"],
                "operator": h["operator"],
            }
            for h in billing.bill_state_history(conn, row["id"])
        ]
    return TmfJSONResponse(
        {
            "id": str(row["id"]),
            "href": tmf_bill.bill_href(row["id"]),
            "billNo": tmf_bill.bill_no(row["id"]),
            "billingAccount": {
                "id": str(row["billing_account_id"]),
                "href": f"/tmf-api/accountManagement/v5/billingAccount/{row['billing_account_id']}",
            },
            "billDate": row["bill_date"],
            "period": {"start": row["bill_period"].lower, "end": row["bill_period"].upper},
            "paymentDueDate": row["payment_due_date"],
            "amountDue": money(row["amount_due"], currency),
            "remainingAmount": money(remaining, currency),
            "state": row["state"],
            "category": row["category"],
            "overdue": remaining > 0 and row["payment_due_date"] < clock.today(),
            "allowedStates": list(settlement.operator_states(current)),
            "charges": charges,
            "sources": sources,
            "history": history,
        }
    )
