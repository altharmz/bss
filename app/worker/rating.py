"""Rating of pay-as-you-go usage, and the barring that follows (docs/decisions/usage.md).

Each tick: rate the closed days of every account with unrated usage (one transaction per
account), queue the `suspend` / `restore` the balances ask for, and send the due ones through
the network adapter.
"""

import logging
from zoneinfo import ZoneInfo

import psycopg

from app.billing import usage_bar, usage_rating
from app.clock import Clock
from app.integrations.network import NetworkRefused, NetworkTemporary, adapter
from app.network import usage as usage_rules
from app.repository import billing
from app.repository import usage as usage_repo
from app.repository import usage_bars as bars
from app.worker import network
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


def process_rating(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> int:
    """One tick; returns how many accounts were rated."""
    ensure_ownership(conn)
    rated = 0
    today = clock.now().astimezone(tz).date()
    for account_id in usage_repo.rating_accounts(conn, today):
        if checkpoint is not None and checkpoint():
            return rated
        try:
            with conn.transaction():
                now = clock.now()
                if usage_rating.rate_account(conn, account_id, now=now, tz=tz) is not None:
                    rated += 1
        except Exception:  # noqa: BLE001 - tax engine down, ...: nothing was written; next tick
            log.exception("rating usage of account %s", account_id)
        if heartbeat is not None:
            heartbeat()
    queue_bars(conn, clock=clock, tz=tz, checkpoint=checkpoint)
    send_bars(conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat)
    return rated


def queue_bars(
    conn: psycopg.Connection, *, clock: Clock, tz: ZoneInfo, checkpoint: Checkpoint | None = None
) -> list:
    """Queue the barring and lifting the balances ask for. Looked at without a lock first;
    only an account that needs a command is locked and looked at again."""
    queued = []
    for account_id in bars.accounts_with_lines(conn):
        if checkpoint is not None and checkpoint():
            break
        today = clock.now().astimezone(tz).date()
        if not usage_bar.wanted(conn, account_id, today):
            continue
        with conn.transaction():
            billing.load_account(conn, account_id)
            now = clock.now()
            queued += usage_bar.evaluate(conn, account_id, now=now, today=now.astimezone(tz).date())
    return queued


def send_bars(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list:
    """Send every due command once (own retry: the backoff of the order commands, never
    exhausted). A line that ended before its command was sent has it cancelled."""
    sent = []
    now = clock.now()
    for due in bars.due(conn, now):
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            bar = bars.get(conn, due["id"], lock=True)
            if bar["state"] != "pending":
                continue
            now = clock.now()
            today = now.astimezone(tz).date()
            line = conn.execute(
                "SELECT status IN ('terminated', 'cancelled', 'aborted')"
                " OR termination_date <= %s FROM product WHERE id = %s",
                (today, bar["product_id"]),
            ).fetchone()
            if line is None or line[0]:
                bars.cancel(conn, bar["id"])
                continue
            request = bar["request"]
            if request is None:
                profile = usage_rules.line_profile(conn, bar["product_id"], today) or {}
                request = network.line_request(
                    conn,
                    {
                        "id": bar["id"],
                        "product_id": bar["product_id"],
                        "kind": bar["kind"],
                        "profile": profile,
                    },
                )
                bars.freeze_request(conn, bar["id"], request)
            try:
                adapter().send(str(bar["id"]), bar["kind"], request)
            except NetworkRefused as refused:
                bars.refuse(conn, bar["id"], code=refused.code, reason=refused.reason)
            except NetworkTemporary as error:
                bars.temporary(conn, bar, error=str(error) or "temporary network error", now=now)
            except Exception as error:  # noqa: BLE001 - unknown outcome: send again later
                log.exception("usage bar %s", bar["id"])
                bars.temporary(conn, bar, error=str(error) or type(error).__name__, now=now)
            else:
                bars.confirm(conn, bar["id"], now=now)
        sent.append(due["id"])
        if heartbeat is not None:
            heartbeat()
    return sent
