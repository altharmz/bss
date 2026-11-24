"""Postpaid dunning: reminders, suspension, restoration (docs/decisions/dunning.md, #385).

Per Active postpaid account (under its lock), the worker computes O, the first business date
the account had an open, non-held bill past its due date, and what it owes past due, and takes
the step the settings make due (app/billing/dunning.py) once: `dunning_step` (PK account, O,
step) is the guard and the record the notification detector reads. The suspension and the
restoration go **through internal TMF622 orders** (modify `status`, cause `dunning`), so orders
alone change products; postpaid orders send no network command (network.md). Restoration
resumes only the products dunning suspended, once the account owes less than
`minimumOverdue` past due (paid, credited, held) or is exempt.

The steps follow the business date, so the full pass runs on the worker's full sweep (the
first tick of a business date, or after a settings change: app/repository/account_review.py);
every other tick looks only at the accounts with a dunning suspension, so a payment restores
the lines within a minute.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import dunning as plan
from app.billing.dunning import Settings
from app.clock import Clock
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import billing, orders, products
from app.repository import dunning as repo
from app.repository._sql import Row
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


class _Rejected(Exception):
    pass


def process_dunning(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    full: bool = True,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[tuple[UUID, str]]:
    """Take every due dunning step once; returns (account, step) of the steps taken.
    `full`: every account with a bill past due; otherwise only the restorations."""
    ensure_ownership(conn)
    now = clock.now()
    today = now.astimezone(tz).date()
    ids = repo.candidates(conn, today, full=full)
    taken: list[tuple[UUID, str]] = []
    if not ids:
        return taken
    settings = repo.settings(conn)  # once per pass: a change applies from the next pass
    for account_id in ids:
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            account = billing.load_account_row(conn, account_id)
            step = _account(conn, account, settings, now, today, full=full)
            if step is not None:
                taken.append((account_id, step))
        if heartbeat is not None:
            heartbeat()
    return taken


def _account(
    conn: psycopg.Connection,
    account: Row,
    settings: Settings,
    now: datetime,
    today: date,
    *,
    full: bool,
) -> str | None:
    """The step taken for the account (locked by the caller), if any."""
    if account["rating_type"] != "postpaid" or account["state"] != "Active":
        return None
    overdue = repo.overdue_of(conn, account["id"], today)  # None when exempt
    o = None if overdue is None else plan.overdue_since([overdue["first_due"]])
    amount = overdue["amount"] if overdue is not None else None
    if o is None or amount is None or not plan.in_dunning(o, amount, settings):
        return "restore" if _restore(conn, account, now, today) else None
    if not full:
        return None
    taken = [row["step"] for row in repo.steps_of(conn, account["id"], o)]
    step = plan.due_step(o, settings, today, taken)
    if step is None:
        return None
    if not repo.record_step(
        conn, account_id=account["id"], o=o, step=step, day=today, amount=amount, now=now
    ):
        return None
    if step == "suspend":
        placed = sum(
            _order(conn, product, account["id"], "suspended", now, today)
            for product in repo.active_products(conn, account["id"], today)
            if not _in_flight(conn, product["id"])
        )
        repo.set_products(conn, account["id"], o, step, placed)
    log.info("dunning %s of account %s (O %s, %s past due)", step, account["id"], o, amount)
    return step


def _restore(conn: psycopg.Connection, account: Row, now: datetime, today: date) -> bool:
    """Resume the products dunning suspended; True if any order was placed. A product whose
    status changed today waits for the next business date (one change per product and day)."""
    placed = 0
    for product in repo.suspended_products(conn, account["id"]):
        if _in_flight(conn, product["id"]):
            continue
        changes = products.status_changes_of(conn, product["id"])
        if changes and changes[-1]["change_date"] >= today:
            continue
        placed += _order(conn, product, account["id"], "active", now, today)
    if not placed:
        return False
    suspension = repo.latest_suspension(conn, account["id"])
    if suspension is not None:
        recorded = repo.record_step(
            conn,
            account_id=account["id"],
            o=suspension["overdue_since"],
            step="restore",
            day=today,
            amount=Decimal("0.00"),
            now=now,
        )
        if recorded:
            repo.set_products(conn, account["id"], suspension["overdue_since"], "restore", placed)
    log.info("dunning restore of account %s: %s product(s)", account["id"], placed)
    return True


def _in_flight(conn: psycopg.Connection, product_id: UUID) -> bool:
    return products.pending_status_change(conn, product_id) is not None or bool(
        orders.open_delete_items(conn, product_id)
    )


def _order(
    conn: psycopg.Connection,
    product: Row,
    account_id: UUID,
    status: str,
    now: datetime,
    today: date,
) -> int:
    """Place the internal order that suspends or resumes the product today: 1 if accepted."""
    item = ItemRequest(
        item_id="1",
        action="modify",
        product_id=product["id"],
        product_status=status,
        dunning=True,
    )
    try:
        with conn.transaction():
            order_id = place_order(
                conn,
                OrderRequest(billing_account_id=account_id, items=(item,), requested_start=today),
                now=now,
                today=today,
            )
            order = orders.get_order(conn, order_id)
            if order["state"] == "rejected":
                errors = orders.error_messages(conn, order_id)
                raise _Rejected("; ".join(e["reason"] for e in errors) or "the order was rejected")
    except _Rejected as rejected:
        log.warning("dunning %s of product %s rejected: %s", status, product["id"], rejected)
        return 0
    except Exception:  # noqa: BLE001 - logged; the restoration is tried again next pass
        log.exception("dunning %s of product %s failed", status, product["id"])
        return 0
    log.info("dunning %s of product %s: order %s", status, product["id"], order_id)
    return 1
