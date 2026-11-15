"""Prepaid effective state at an injected business date; reads never emit events."""

from app.repository._sql import fetch_all, fetch_one


def effective_state(product, account, grant, today):
    if product["status"] in {"terminated", "cancelled", "aborted"}:
        return product["status"], None
    if product["termination_date"] is not None and product["termination_date"] <= today:
        return "terminated", None
    if product["manually_suspended"]:
        return "suspended", "lifecycle" if product.get("lifecycle_suspended") else "manual"
    if account["state"] != "Active":
        return "suspended", "account"
    if product.get("grantless"):
        # an add-on or pay-as-you-go product has no funded interval: the order that
        # created it activates it, and it stays active until its end date
        pending = product["status"] in {"created", "pendingActive"}
        return ("pendingActive" if pending else "active"), None
    if grant is not None and grant["period"].lower <= today < grant["period"].upper:
        # an accepted termination keeps service until its date
        return ("active" if product["termination_date"] is None else "pendingTerminate"), None
    if grant is None:
        return "pendingActive", None
    return "suspended", "funding"


def latest_grant(conn, product_id):
    return fetch_one(
        conn,
        "SELECT * FROM funded_interval WHERE product_id = %s"
        " ORDER BY lower(period) DESC, authorized_at DESC LIMIT 1",
        (product_id,),
    )


# A grant of the same product after the grant `g`: the latest grant (`latest_grant`, by
# first day, then authorization) is the one no such grant follows. Grants of a product
# overlap only when one supersedes the other, perhaps from the same first day, so the
# authorization (then the id, for a complete order) breaks that tie. For
# `NOT EXISTS (...)` on `funded_interval g`; funded_interval_latest_idx serves it.
LATER_GRANT = (
    "SELECT FROM funded_interval l WHERE l.product_id = g.product_id"
    " AND lower(l.period) >= lower(g.period)"
    " AND (lower(l.period), l.authorized_at, l.id) > (lower(g.period), g.authorized_at, g.id)"
)


def latest_grants(conn, product_ids):
    """`latest_grant` of each product that has one, in one statement (#305)."""
    if not product_ids:
        return {}
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (product_id) * FROM funded_interval WHERE product_id = ANY(%s)"
        " ORDER BY product_id, lower(period) DESC, authorized_at DESC",
        (sorted(set(product_ids)),),
    )
    return {row["product_id"]: row for row in rows}


def view(conn, product, account, today):
    grant = latest_grant(conn, product["id"])
    status, cause = effective_state(product, account, grant, today)
    shortfall = fetch_one(
        conn,
        "SELECT s.* FROM prepaid_funding_shortfall s JOIN prepaid_command c ON c.id = s.command_id"
        " WHERE c.state = 'pending' AND c.billing_account_id = %s AND ("
        " c.request->>'product_id' = %s OR c.request->>'previous_interval_id' = %s"
        " OR EXISTS (SELECT FROM jsonb_array_elements(COALESCE(c.request->'intervals', '[]')) q"
        " WHERE q->>'product_id' = %s) OR c.request->'products' ? %s)"
        " ORDER BY s.observed_at DESC LIMIT 1",
        (
            account["id"],
            str(product["id"]),
            str(grant["id"]) if grant else "",
            str(product["id"]),
            str(product["id"]),
        ),
    )
    return {
        "status": status,
        "authorizedAt": grant["authorized_at"] if grant else None,
        "paidThrough": grant["period"].upper
        if grant
        else product.get("termination_date")
        if product.get("grantless")
        else None,
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
