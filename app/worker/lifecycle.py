"""The prepaid line lifecycle: grace, suspension, deactivation (docs/decisions/prepaid.md).

Each tick, per prepaid account (under its lock), the worker compares every open line with
the dates the settings give (app/billing/lifecycle.py) and takes the due transition **through
an internal TMF622 order**, so the network adapter sends `suspend`, `restore` or `deactivate`
and orders alone change products. A lifecycle suspension does not block renewal: a top-up or a
card payment renews the plan and the next tick resumes the line. One transition per product per
tick; nothing is placed while a status or delete order of the product is in flight, and each
action is tried once per business date.
"""

import logging
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import lifecycle as plan
from app.billing.lifecycle import funded_state, is_payg
from app.clock import Clock
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import billing, funding, orders, products
from app.repository import lifecycle as repo
from app.repository._sql import Row, fetch_all, fetch_one
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

# A semi-join, not DISTINCT over a join: no aggregate over every open line (#332, 210 -> 151 ms
# on 100k accounts). The full pass runs once per business date; its two scans are intended.
ACCOUNTS = (
    "SELECT a.id FROM billing_account a"
    " WHERE a.rating_type = 'prepaid' AND a.state = 'Active'"
    " AND EXISTS (SELECT FROM product p WHERE p.billing_account_id = a.id"
    " AND p.status NOT IN ('terminated', 'cancelled', 'aborted') AND p.termination_date IS NULL"
    " AND p.parent_product_id IS NULL)"
)


class _Rejected(Exception):
    pass


def process_lifecycle(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
    accounts: list[UUID] | None = None,
) -> list[UUID]:
    """Take every due lifecycle transition once; returns the products acted on. `accounts`
    limits the pass to those accounts (app/repository/account_review.py); None: all."""
    ensure_ownership(conn)
    acted: list[UUID] = []
    if accounts is None:
        rows = fetch_all(conn, ACCOUNTS + " ORDER BY a.id")
    else:
        rows = fetch_all(conn, ACCOUNTS + " AND a.id = ANY(%s) ORDER BY a.id", (accounts,))
    for row in rows:
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            billing.load_account(conn, row["id"])
            now = clock.now()
            acted += _account(conn, row["id"], now, now.astimezone(tz).date())
        if heartbeat is not None:
            heartbeat()
    return acted


def _in_flight(conn: psycopg.Connection, product_id: UUID) -> bool:
    return products.pending_status_change(conn, product_id) is not None or bool(
        orders.open_delete_items(conn, product_id)
    )


def _account(conn: psycopg.Connection, account_id: UUID, now: datetime, today: date) -> list[UUID]:
    account = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account_id,))
    rows = fetch_all(
        conn,
        "SELECT * FROM product WHERE billing_account_id = %s AND parent_product_id IS NULL"
        " AND status NOT IN ('terminated', 'cancelled', 'aborted') AND termination_date IS NULL"
        " ORDER BY id FOR UPDATE",
        (account_id,),
    )
    settings = repo.settings(conn)
    spendable = None
    if any(is_payg(p) for p in rows):
        spendable = funding.load(conn, account_id).spendable
        zero = account["balance_zero_since"]
        if spendable <= 0 and zero is None:
            conn.execute(
                "UPDATE billing_account SET balance_zero_since = %s WHERE id = %s",
                (today, account_id),
            )
            account["balance_zero_since"] = today
        elif spendable > 0 and zero is not None:
            conn.execute(
                "UPDATE billing_account SET balance_zero_since = NULL WHERE id = %s", (account_id,)
            )
            account["balance_zero_since"] = None
    acted = []
    for product in rows:
        if product["manually_suspended"] and not product["lifecycle_suspended"]:
            continue  # an operator's pause: the lifecycle leaves it alone
        if _in_flight(conn, product["id"]):
            continue
        funded, since = funded_state(conn, product, account, spendable, settings, today)
        action = _due(product, funded, since, settings, today)
        if action is None:
            continue
        if action == "resume" and account["state"] != "Active":
            continue
        if _take(conn, product, account_id, action, now, today):
            acted.append(product["id"])
    return acted


def _due(product: Row, funded: bool, since: date | None, settings, today: date) -> str | None:
    suspended = bool(product["lifecycle_suspended"])
    if funded:
        return "resume" if suspended else None
    if since is None:
        return None
    due = plan.schedule(since, settings)
    if today >= due.deactivate_on:
        return "deactivate"
    if today >= due.suspend_on and not suspended:
        return "suspend"
    return None


def _take(conn, product: Row, account_id: UUID, action: str, now: datetime, today: date) -> bool:
    event = repo.claim_attempt(
        conn, product_id=product["id"], account_id=account_id, action=action, day=today, now=now
    )
    if event is None:
        return False  # tried today already; a refusal is retried on the next business date
    item = (
        ItemRequest(item_id="1", action="delete", product_id=product["id"], lifecycle=True)
        if action == "deactivate"
        else ItemRequest(
            item_id="1",
            action="modify",
            product_id=product["id"],
            product_status="suspended" if action == "suspend" else "active",
            lifecycle=True,
        )
    )
    start = today
    if action == "resume":
        # one status change per product and day: after a suspension taken today the
        # resumption is dated the next business date
        latest = products.status_changes_of(conn, product["id"])
        if latest and latest[-1]["change_date"] >= today:
            start = latest[-1]["change_date"] + timedelta(days=1)
    try:
        with conn.transaction():
            order_id = place_order(
                conn,
                OrderRequest(billing_account_id=account_id, items=(item,), requested_start=start),
                now=now,
                today=today,
            )
            order = orders.get_order(conn, order_id)
            if order["state"] == "rejected":
                errors = orders.error_messages(conn, order_id)
                raise _Rejected("; ".join(e["reason"] for e in errors) or "the order was rejected")
    except _Rejected as rejected:
        repo.settle(conn, event, order_id=None, rejected=str(rejected))
        log.warning("lifecycle %s of product %s rejected: %s", action, product["id"], rejected)
        return False
    except Exception as error:  # noqa: BLE001 - tried again next business date
        repo.settle(conn, event, order_id=None, rejected=str(error) or type(error).__name__)
        log.exception("lifecycle %s of product %s failed", action, product["id"])
        return False
    repo.settle(conn, event, order_id=order_id, rejected=None)
    log.info("lifecycle %s of product %s: order %s", action, product["id"], order_id)
    return True
