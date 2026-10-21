"""Prepaid effective state at an injected business date; reads never emit events."""

from app.repository._sql import fetch_one


def effective_state(product, account, grant, today):
    if product["status"] in {"terminated", "cancelled", "aborted"}:
        return product["status"], None
    if product["termination_date"] is not None and product["termination_date"] <= today:
        return "terminated", None
    if product["manually_suspended"]:
        return "suspended", "manual"
    if account["state"] != "Active":
        return "suspended", "account"
    if grant is not None and grant["period"].lower <= today < grant["period"].upper:
        return "active", None
    if grant is None:
        return "pendingActive", None
    return "suspended", "funding"


def view(conn, product, account, today):
    grant = fetch_one(
        conn,
        "SELECT * FROM funded_interval WHERE product_id = %s ORDER BY upper(period) DESC LIMIT 1",
        (product["id"],),
    )
    status, cause = effective_state(product, account, grant, today)
    shortfall = fetch_one(
        conn,
        "SELECT s.* FROM prepaid_funding_shortfall s JOIN prepaid_command c ON c.id = s.command_id"
        " WHERE c.state = 'pending' AND c.billing_account_id = %s AND ("
        " c.request->>'product_id' = %s OR c.request->>'previous_interval_id' = %s"
        " OR EXISTS (SELECT FROM jsonb_array_elements(COALESCE(c.request->'intervals', '[]')) q"
        " WHERE q->>'product_id' = %s)) ORDER BY s.observed_at DESC LIMIT 1",
        (account["id"], str(product["id"]), str(grant["id"]) if grant else "", str(product["id"])),
    )
    return {
        "status": status,
        "authorizedAt": grant["authorized_at"] if grant else None,
        "paidThrough": grant["period"].upper if grant else None,
        "suspensionCause": cause,
        "needs_topup": None
        if shortfall is None
        else {
            "required": shortfall["required"],
            "available": shortfall["available"],
            "shortfall": shortfall["required"] - shortfall["available"],
            "observedAt": shortfall["observed_at"],
        },
    }
