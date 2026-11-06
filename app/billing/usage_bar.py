"""Barring a pay-as-you-go line whose balance ran out, and lifting it (docs/decisions/usage.md).

The balance is the account's spendable money (ledger less holds). At zero or below, each
active pay-as-you-go line that is not barred gets a `suspend`; above zero, each barred line
(not suspended by an operator) gets a `restore`. The commands are rows of `usage_bar`, sent
by the worker through the network adapter. Not real-time: usage is rated after its day closes.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg

from app.network import kind as offering_kind
from app.repository import billing
from app.repository import usage_bars as bars


def spendable(conn: psycopg.Connection, account_id: UUID) -> Decimal:
    return billing.account_balance(conn, account_id) - bars.held(conn, account_id)


def wanted(conn: psycopg.Connection, account_id: UUID, today: date) -> list[tuple[UUID, str]]:
    """The commands the balance asks for now, as (product, kind); read-only."""
    lines = [
        line
        for line in bars.payg_lines(conn, account_id, today)
        if offering_kind.kind_of(line["details"]) == offering_kind.PAY_AS_YOU_GO
    ]
    if not lines:
        return []
    out = []
    empty = spendable(conn, account_id) <= 0
    for line in lines:
        last = bars.latest(conn, line["id"])
        barred = last is not None and last["kind"] == "suspend"
        if empty and not barred and line["status"] == "active":
            out.append((line["id"], "suspend"))
        elif not empty and barred and not line["manually_suspended"]:
            out.append((line["id"], "restore"))
    return out


def evaluate(
    conn: psycopg.Connection, account_id: UUID, *, now: datetime, today: date
) -> list[UUID]:
    """Queue the commands the balance asks for; the caller holds the account lock. Returns
    the new command ids."""
    return [
        bars.create(conn, product, kind, now)["id"]
        for product, kind in wanted(conn, account_id, today)
    ]


def line_state(conn: psycopg.Connection, product_id: UUID, today: date) -> dict | None:
    """The pay-as-you-go view of a line for the internal usage summary: the account's balance
    (spendable), the barring state and the records still unrated; None when the line is
    not a pay-as-you-go line."""
    from app.repository import products
    from app.repository import usage as usage_repo

    product = products.get_product(conn, product_id)
    if product is None or not product["grantless"] or product["parent_product_id"] is not None:
        return None
    offering = products.offering_on(conn, product_id, max(today, product["start_date"]))
    details = conn.execute(
        "SELECT details FROM product_offering WHERE id = %s", (offering,)
    ).fetchone()[0]
    if offering_kind.kind_of(details) != offering_kind.PAY_AS_YOU_GO:
        return None
    account_id = product["billing_account_id"]
    currency = conn.execute(
        "SELECT currency FROM billing_account WHERE id = %s", (account_id,)
    ).fetchone()[0]
    last = bars.latest(conn, product_id)
    return {
        "balance": spendable(conn, account_id),
        "currency": currency,
        "barred": last is not None and last["kind"] == "suspend",
        "barring": None
        if last is None
        else {
            "command": last["kind"],
            "state": last["state"],
            "lastError": last["last_error"],
            "createdAt": last["created_at"],
        },
        "unratedRecords": usage_repo.unrated_count(conn, product_id),
    }
