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


# `effective_state(...)[0]` in SQL, for a product `p` of the account `a` that is not ended,
# with `g` its latest grant (`LATEST_GRANT`, NULL columns when none) at the business date
# %(today)s. The same branches in the same order; tests/test_full_review.py checks
# the two agree. It lets a full entitlement pass find the products to change in one
# statement (#404).
EFFECTIVE_STATUS = (
    "CASE WHEN p.termination_date IS NOT NULL AND p.termination_date <= %(today)s"
    " THEN 'terminated'"
    " WHEN p.manually_suspended THEN 'suspended'"
    " WHEN a.state IS DISTINCT FROM 'Active' THEN 'suspended'"
    " WHEN p.grantless THEN CASE WHEN p.status::text IN ('created', 'pendingActive')"
    " THEN 'pendingActive' ELSE 'active' END"
    " WHEN g.period IS NULL THEN 'pendingActive'"
    " WHEN lower(g.period) <= %(today)s AND %(today)s < upper(g.period)"
    " THEN CASE WHEN p.termination_date IS NULL THEN 'active' ELSE 'pendingTerminate' END"
    " ELSE 'suspended' END"
)

# The latest grant of the product `p` (`latest_grant`), as `LEFT JOIN LATERAL (...) g ON true`.
LATEST_GRANT = (
    "SELECT period FROM funded_interval WHERE product_id = p.id"
    " ORDER BY lower(period) DESC, authorized_at DESC,"
    " supersedes_interval_id IS NULL, id DESC LIMIT 1"
)


def status(conn, product, account, today):
    """The product's effective status alone: `view(...)["status"]` without its other reads."""
    return effective_state(product, account, latest_grant(conn, product["id"]), today)[0]


def latest_grant(conn, product_id):
    return fetch_one(
        conn,
        "SELECT * FROM funded_interval WHERE product_id = %s"
        " ORDER BY lower(period) DESC, authorized_at DESC,"
        " supersedes_interval_id IS NULL, id DESC LIMIT 1",
        (product_id,),
    )


# A grant of the same product after the grant `g`: the latest grant (`latest_grant`) is
# the one no such grant follows. Grants of a product overlap only when one supersedes the
# other, perhaps from the same first day, so the order is the first day, then the
# authorization, then the superseding grant before the one it supersedes (the two can
# share an authorization time, as under a test clock), then the id: the same total order
# as every `ORDER BY lower(period) DESC, authorized_at DESC, supersedes_interval_id IS
# NULL, id DESC`. For `NOT EXISTS (...)` on `funded_interval g`;
# funded_interval_latest_idx serves it.
LATER_GRANT = (
    "SELECT FROM funded_interval l WHERE l.product_id = g.product_id"
    " AND lower(l.period) >= lower(g.period)"
    " AND (lower(l.period), l.authorized_at, l.supersedes_interval_id IS NOT NULL, l.id)"
    " > (lower(g.period), g.authorized_at, g.supersedes_interval_id IS NOT NULL, g.id)"
)


def latest_grants(conn, product_ids):
    """`latest_grant` of each product that has one, in one statement (#305)."""
    if not product_ids:
        return {}
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (product_id) * FROM funded_interval WHERE product_id = ANY(%s)"
        " ORDER BY product_id, lower(period) DESC, authorized_at DESC,"
        " supersedes_interval_id IS NULL, id DESC",
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
