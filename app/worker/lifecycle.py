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

from app.billing import entitlement
from app.billing import lifecycle as plan
from app.billing.lifecycle import Settings, funded_state, is_payg
from app.clock import Clock
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import billing, funding, orders, products
from app.repository import lifecycle as repo
from app.repository._sql import Row, fetch_all
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

# A semi-join, not DISTINCT over a join: no aggregate over every open line (#332, 210 -> 151 ms
# on 100k accounts).
ACCOUNTS = (
    "SELECT a.id FROM billing_account a"
    " WHERE a.rating_type = 'prepaid' AND a.state = 'Active'"
    " AND EXISTS (SELECT FROM product p WHERE p.billing_account_id = a.id"
    " AND p.status NOT IN ('terminated', 'cancelled', 'aborted') AND p.termination_date IS NULL"
    " AND p.parent_product_id IS NULL)"
)

# The full pass (#404) evaluates only the accounts where `_account` can change something, in
# its terms (`funded_state`, `_due`): a line funded again while the lifecycle has it suspended
# (resume), or an unfunded line whose suspension (not suspended: `grace_days` after its first
# unfunded day) or deactivation (suspended: `grace_days + suspension_days` after it) is due;
# and a pay-as-you-go account whose zero-balance date must be set or cleared. A plan is funded
# while its latest grant covers today or starts later, or when it has none; it is unfunded
# from the end of that grant. A pay-as-you-go line is funded while its account's balance is
# above zero (%(funded)s, read before this query) and unfunded `payg_zero_balance_days` after
# the balance reached zero. An operator's pause is left alone. Any other account is funded or
# not yet due and evaluating it changes nothing. `_account` still decides under the lock.
DUE = (
    " AND EXISTS (SELECT FROM product p"  # noqa: S608 - LATEST_GRANT is code
    " LEFT JOIN LATERAL (" + entitlement.LATEST_GRANT + ") g ON true"
    " WHERE p.billing_account_id = a.id AND p.parent_product_id IS NULL"
    " AND p.status NOT IN ('terminated', 'cancelled', 'aborted') AND p.termination_date IS NULL"
    " AND NOT (coalesce(p.manually_suspended, false)"
    " AND NOT coalesce(p.lifecycle_suspended, false))"
    " AND CASE WHEN p.grantless THEN CASE WHEN a.id = ANY(%(funded)s::uuid[])"
    " THEN coalesce(p.lifecycle_suspended, false) OR a.balance_zero_since IS NOT NULL"
    " ELSE a.balance_zero_since IS NULL OR a.balance_zero_since + %(payg)s::int + %(grace)s::int"
    " + CASE WHEN p.lifecycle_suspended THEN %(suspension)s::int ELSE 0 END <= %(today)s END"
    " WHEN g.period IS NULL OR lower(g.period) > %(today)s OR %(today)s < upper(g.period)"
    " THEN coalesce(p.lifecycle_suspended, false)"
    " ELSE upper(g.period) + %(grace)s::int"
    " + CASE WHEN p.lifecycle_suspended THEN %(suspension)s::int ELSE 0 END <= %(today)s END)"
)

# The accounts with a pay-as-you-go line: the full pass reads their balances for `DUE`.
PAYG = (
    ACCOUNTS + " AND EXISTS (SELECT FROM product p"
    " WHERE p.billing_account_id = a.id AND p.parent_product_id IS NULL AND p.grantless"
    " AND p.status NOT IN ('terminated', 'cancelled', 'aborted') AND p.termination_date IS NULL)"
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
    # once per pass (#305): a change of the settings applies from the next pass
    settings = repo.settings(conn)
    if accounts is None:
        rows = _due_accounts(conn, settings, clock.now().astimezone(tz).date())
    else:
        rows = fetch_all(conn, ACCOUNTS + " AND a.id = ANY(%s) ORDER BY a.id", (accounts,))
    for row in rows:
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            account = billing.load_account_row(conn, row["id"])
            now = clock.now()
            acted += _account(conn, account, settings, now, now.astimezone(tz).date())
        if heartbeat is not None:
            heartbeat()
    return acted


def _due_accounts(conn: psycopg.Connection, settings: Settings, today: date) -> list[Row]:
    """The accounts of a full pass (`DUE`), in three statements whatever their number."""
    payg = [row["id"] for row in fetch_all(conn, PAYG)]
    funded = [a for a, spendable in funding.spendable_many(conn, payg).items() if spendable > 0]
    params = {
        "funded": funded,
        "today": today,
        "grace": settings.grace_days,
        "suspension": settings.suspension_days,
        "payg": settings.payg_zero_balance_days,
    }
    return fetch_all(conn, ACCOUNTS + DUE + " ORDER BY a.id", params)


def _in_flight(conn: psycopg.Connection, product_id: UUID) -> bool:
    return products.pending_status_change(conn, product_id) is not None or bool(
        orders.open_delete_items(conn, product_id)
    )


def _account(
    conn: psycopg.Connection, account: Row, settings: Settings, now: datetime, today: date
) -> list[UUID]:
    """The account's due transitions; `account` is its row, locked by the caller."""
    account_id = account["id"]
    rows = fetch_all(
        conn,
        "SELECT * FROM product WHERE billing_account_id = %s AND parent_product_id IS NULL"
        " AND status NOT IN ('terminated', 'cancelled', 'aborted') AND termination_date IS NULL"
        " ORDER BY id FOR UPDATE",
        (account_id,),
    )
    spendable = None
    if any(is_payg(p) for p in rows):
        spendable = funding.load(conn, account_id, locked=account).spendable
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
        # Unfunded is only a plan past its grant (since = the grant's end) or a pay-as-you-go
        # line at zero, whose balance_zero_since `_account` set before this call.
        return None  # pragma: no cover - an unfunded line always has its first unfunded day
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
