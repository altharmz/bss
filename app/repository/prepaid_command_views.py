"""Read views of the prepaid command journal for the internal API (P3i).

Read-only: no ownership or identity lock. Links are derived from the retained request,
the command's own run and grants, never stored twice.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one

_SELECT = (
    "SELECT c.*, a.name AS account_name, a.currency FROM prepaid_command c"
    " JOIN billing_account a ON a.id = c.billing_account_id"
)


def get(conn: psycopg.Connection, command_id: UUID) -> Row | None:
    return fetch_one(conn, _SELECT + " WHERE c.id = %s", (command_id,))


def page(
    conn: psycopg.Connection,
    *,
    account_id: UUID | None,
    state: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], int]:
    """Newest acceptance first; ties by id."""
    params = {"account": account_id, "state": state, "offset": offset, "limit": limit}
    rows = fetch_all(
        conn,
        _SELECT + " WHERE (%(account)s::uuid IS NULL OR c.billing_account_id = %(account)s)"
        " AND (%(state)s::text IS NULL OR c.state = %(state)s)"
        " ORDER BY c.created_at DESC, c.id DESC OFFSET %(offset)s LIMIT %(limit)s",
        params,
    )
    total = conn.execute(
        "SELECT count(*) FROM prepaid_command c"
        " WHERE (%(account)s::uuid IS NULL OR c.billing_account_id = %(account)s)"
        " AND (%(state)s::text IS NULL OR c.state = %(state)s)",
        params,
    ).fetchone()[0]
    return rows, total


def _request_products(request: dict[str, Any]) -> set[str]:
    ids = set(request.get("products", [])) | set(request.get("terminations", []))
    ids |= {q["product_id"] for q in request.get("intervals", [])}
    if "product_id" in request:
        ids.add(request["product_id"])
    return ids


def links(conn: psycopg.Connection, rows: list[Row]) -> dict[UUID, dict[str, Any]]:
    """Per command: its order, products, run, bills and current needs-top-up reason."""
    ids = [row["id"] for row in rows]
    out: dict[UUID, dict[str, Any]] = {
        row["id"]: {
            "order": row["request"].get("order_id") if row["kind"] == "order" else None,
            "products": _request_products(row["request"]),
            "run": None,
            "bills": [],
            "shortfall": None,
        }
        for row in rows
    }
    if not ids:
        return out
    previous = {
        row["request"]["previous_interval_id"]: row["id"]
        for row in rows
        if "previous_interval_id" in row["request"]
    }
    for grant in fetch_all(
        conn,
        "SELECT id, command_id, product_id FROM funded_interval"
        " WHERE command_id = ANY(%s) OR id::text = ANY(%s)",
        (ids, list(previous)),
    ):
        owner = grant["command_id"] if grant["command_id"] in out else None
        if owner is not None:
            out[owner]["products"].add(str(grant["product_id"]))
        renewal = previous.get(str(grant["id"]))
        if renewal is not None:
            out[renewal]["products"].add(str(grant["product_id"]))
    runs = fetch_all(
        conn, "SELECT id, run_date, command_id FROM billing_run WHERE command_id = ANY(%s)", (ids,)
    )
    bills: dict[UUID, list[UUID]] = defaultdict(list)
    for bill in fetch_all(
        conn,
        "SELECT id, billing_run_id FROM customer_bill WHERE billing_run_id = ANY(%s)"
        " ORDER BY bill_date, id",
        ([run["id"] for run in runs],),
    ):
        bills[bill["billing_run_id"]].append(bill["id"])
    for run in runs:
        out[run["command_id"]]["run"] = run
        out[run["command_id"]]["bills"] = bills[run["id"]]
    for shortfall in fetch_all(
        conn,
        "SELECT DISTINCT ON (s.command_id) s.* FROM prepaid_funding_shortfall s"
        " JOIN prepaid_command c ON c.id = s.command_id"
        " WHERE c.state = 'pending' AND s.command_id = ANY(%s)"
        " ORDER BY s.command_id, s.observed_at DESC",
        (ids,),
    ):
        out[shortfall["command_id"]]["shortfall"] = shortfall
    for link in out.values():
        link["products"] = sorted(link["products"])
    return out
