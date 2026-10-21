"""Dispatch retained purchases and renewals; producers remain gated.

One account pass keeps its lock across ordered attempts. Each posting uses a
savepoint, so a failed command leaves no money or document effects. Technical
diagnostics commit with the pass after that savepoint has rolled back.
"""

import logging
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import entitlement
from app.billing.funding import InsufficientFunds
from app.billing.prepaid import post_interval
from app.clock import Clock
from app.events import emit
from app.repository import billing
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_all, fetch_one
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


def process_commands(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """Attempt each eligible command once per tick, independently of daily gaps.

    Funding shortfalls retain a needs-top-up reason and remain pending for the
    next tick. They never become technical failures. Unsupported transfer commands
    are left to their future dispatcher.
    """
    ensure_ownership(conn)
    now = clock.now()
    candidates = fetch_all(
        conn,
        "SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f"
        " ON f.id::text = c.request->>'previous_interval_id'"
        " WHERE c.state <> 'completed'"
        " AND c.kind IN ('order', 'renewal') AND c.due_date <= %s"
        " AND (c.next_retry_at IS NULL OR c.next_retry_at <= %s)"
        " ORDER BY c.billing_account_id, c.due_date,"
        " COALESCE(c.request->>'product_id', f.product_id::text, c.id::text), c.id",
        (now.astimezone(tz).date(), now),
    )
    accounts = {}
    for cmd in candidates:
        accounts.setdefault(cmd["billing_account_id"], []).append(cmd)
    attempted = []
    for account_id, pending in accounts.items():
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            # Command identity precedes account locks, as in producers/posting.
            for cmd in sorted(pending, key=lambda c: c["id"]):
                commands.lock_identity(conn, cmd["id"])
            billing.load_account(conn, account_id)
            for cmd in pending:
                now = clock.now()  # funds committed before the account lock are visible
                current = commands.get(conn, cmd["id"])
                if current["state"] == "completed" or (
                    current["next_retry_at"] is not None and current["next_retry_at"] > now
                ):
                    continue
                try:
                    post_interval(conn, cmd["id"], now=now, tz=tz)
                except InsufficientFunds as shortfall:
                    commands.record_shortfall(
                        conn,
                        cmd["id"],
                        required=shortfall.required,
                        available=shortfall.available,
                        now=now,
                    )
                    log.info("prepaid command %s needs top-up: %s", cmd["id"], shortfall)
                except Exception as error:
                    commands.fail(conn, cmd["id"], str(error) or type(error).__name__, now=now)
                    log.exception("prepaid command %s failed", cmd["id"])
                attempted.append(cmd["id"])
        if heartbeat is not None:
            heartbeat()
    return attempted


def observe_entitlements(conn, *, clock, tz, checkpoint=None, heartbeat=None):
    """Persist observed entitlement transitions once, under account/product locks."""
    ensure_ownership(conn)
    accounts = fetch_all(
        conn, "SELECT id FROM billing_account WHERE rating_type = 'prepaid' ORDER BY id"
    )
    for account in accounts:
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            billing.load_account(conn, account["id"])
            account = fetch_one(
                conn, "SELECT * FROM billing_account WHERE id = %s", (account["id"],)
            )
            rows = fetch_all(
                conn,
                "SELECT * FROM product WHERE billing_account_id = %s"
                " AND status NOT IN ('terminated', 'cancelled', 'aborted') ORDER BY id FOR UPDATE",
                (account["id"],),
            )
            now = clock.now()
            today = now.astimezone(tz).date()
            for product in rows:
                state = entitlement.view(conn, product, account, today)["status"]
                if state != product["status"]:
                    conn.execute(
                        "UPDATE product SET status = %s WHERE id = %s", (state, product["id"])
                    )
                    emit.product(conn, product["id"], "StateChange", at=now, today=today)
        if heartbeat is not None:
            heartbeat()
