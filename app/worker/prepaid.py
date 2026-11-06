"""Produce due renewals and dispatch retained purchases and renewals.

One account pass keeps its lock across ordered attempts. Each posting uses a
savepoint, so a failed command leaves no money or document effects. Technical
diagnostics commit with the pass after that savepoint has rolled back.
"""

import logging
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import entitlement, transfer_fee
from app.billing.funding import InsufficientFunds
from app.billing.offering_change import post_offering_change
from app.billing.one_time import post_charges
from app.billing.prepaid import post_interval
from app.billing.status_change import post_status_change, renewal_ids
from app.billing.termination import post_termination
from app.clock import Clock
from app.events import emit
from app.repository import balance_actions, billing, funding
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_all, fetch_one
from app.repository.prepaid_commands import renewal_id
from app.worker import network, payments
from app.worker import ports as port_numbers
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

# A product's latest grant ended on or before today, with nothing blocking renewal
# and no renewal produced for it yet. Manual and account suspension block renewal.
_DUE_RENEWALS = (
    "SELECT g.id, g.product_id, g.billing_account_id, g.due FROM ("
    " SELECT DISTINCT ON (product_id) id, product_id, billing_account_id, upper(period) AS due"
    " FROM funded_interval ORDER BY product_id, lower(period) DESC, authorized_at DESC) g"
    " JOIN product p ON p.id = g.product_id JOIN billing_account a ON a.id = g.billing_account_id"
    " WHERE g.due <= %(today)s AND a.rating_type = 'prepaid' AND a.state = 'Active'"
    " AND NOT (p.manually_suspended AND NOT p.lifecycle_suspended)"
    " AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
    " AND (p.termination_date IS NULL OR p.termination_date > %(today)s)"
    " AND NOT EXISTS (SELECT FROM prepaid_command c WHERE c.kind = 'renewal'"
    " AND c.request->>'previous_interval_id' = g.id::text)"
    " AND (%(account)s::uuid IS NULL OR g.billing_account_id = %(account)s)"
    " ORDER BY g.billing_account_id, g.due, g.product_id"
)


def produce_renewals(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """Queue one renewal per expired latest grant, due date then product, per account.

    A late producer keeps the paid-through due date; posting buys from today.
    Candidates are revalidated under command identity and account locks.
    """
    ensure_ownership(conn)
    today = clock.now().astimezone(tz).date()
    accounts = {}
    for row in fetch_all(conn, _DUE_RENEWALS, {"today": today, "account": None}):
        accounts.setdefault(row["billing_account_id"], []).append(row)
    produced = []
    for account_id, due in accounts.items():
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            ids = {renewal_id(row["id"]) for row in due}
            for command_id in sorted(ids):
                commands.lock_identity(conn, command_id)
            billing.load_account(conn, account_id)
            now = clock.now()
            today = now.astimezone(tz).date()
            for row in fetch_all(conn, _DUE_RENEWALS, {"today": today, "account": account_id}):
                command_id = renewal_id(row["id"])
                if command_id not in ids:
                    continue  # appeared after discovery: its identity is not locked yet
                commands.enqueue(
                    conn,
                    command_id,
                    account_id,
                    kind="renewal",
                    due_date=row["due"],
                    request={"previous_interval_id": str(row["id"])},
                    now=now,
                )
                produced.append(command_id)
        if heartbeat is not None:
            heartbeat()
    return produced


def _renewal_blocked(conn, cmd, today) -> str | None:
    """Manual/account suspension leaves a renewal pending, unattempted ("blocked");
    an ended product retires it ("ended")."""
    row = fetch_one(
        conn,
        "SELECT p.status IN ('terminated', 'cancelled', 'aborted')"
        " OR p.termination_date <= %s AS ended,"
        " a.state <> 'Active' OR (p.manually_suspended AND NOT p.lifecycle_suspended) AS blocked"
        " FROM funded_interval f JOIN product p ON p.id = f.product_id"
        " JOIN billing_account a ON a.id = f.billing_account_id WHERE f.id::text = %s",
        (today, cmd["request"].get("previous_interval_id")),
    )
    if row is None:
        return None
    if not row["ended"] and not row["blocked"]:
        product = fetch_one(
            conn,
            "SELECT product_id FROM funded_interval WHERE id::text = %s",
            (cmd["request"].get("previous_interval_id"),),
        )
        if product is not None and network.waiting_for_profile(conn, product["product_id"]):
            return "blocked"  # the network has not confirmed the offering change yet
    return "ended" if row["ended"] else "blocked" if row["blocked"] else None


def _retire(conn, cmd, now):
    """A renewal of an ended product completes as cancelled, releasing any owned hold."""
    hold = fetch_one(
        conn, "SELECT hold_id FROM funding_command_hold WHERE command_id = %s", (cmd["id"],)
    )
    if hold is not None:
        funding.release(
            conn, cmd["billing_account_id"], hold["hold_id"], released_at=now, command_id=cmd["id"]
        )
    commands.complete(conn, cmd["id"], {"outcome": "cancelled", "reason": "productEnded"}, now=now)


def process_commands(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """Attempt each eligible command once per tick, independently of daily gaps.

    Funding shortfalls retain a needs-top-up reason and remain pending. They are
    attempted again after a later credit (a payment or a released hold) or on a
    later business date, never as technical failures. A renewal of an ended product
    is retired at once. Transfers complete in their own pass afterwards
    (`process_transfers`).
    """
    ensure_ownership(conn)
    now = clock.now()
    network.process_network(conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat)
    payments.process_payments(conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat)
    port_numbers.process_ports(conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat)
    balance_actions.release_all_expired(conn, now)
    candidates = fetch_all(
        conn,
        "SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f"
        " ON f.id::text = c.request->>'previous_interval_id'"
        " LEFT JOIN product p ON p.id = f.product_id"
        " LEFT JOIN LATERAL (SELECT observed_at FROM prepaid_funding_shortfall"
        " WHERE command_id = c.id ORDER BY observed_at DESC LIMIT 1) s ON true"
        " WHERE c.state <> 'completed'"
        " AND c.kind IN ('order', 'renewal') AND c.due_date <= %(today)s"
        " AND (c.next_retry_at IS NULL OR c.next_retry_at <= %(now)s)"
        " AND (s.observed_at IS NULL OR c.attempted_at > s.observed_at"
        " OR p.termination_date <= %(today)s"
        " OR (s.observed_at AT TIME ZONE %(tz)s)::date < %(today)s"
        " OR EXISTS (SELECT FROM payment p WHERE p.billing_account_id = c.billing_account_id"
        " AND p.status = 'done' AND p.received_at > s.observed_at)"
        " OR EXISTS (SELECT FROM funding_hold h WHERE h.billing_account_id = c.billing_account_id"
        " AND h.released_at > s.observed_at))"
        " ORDER BY c.billing_account_id, c.due_date,"
        " COALESCE(c.request->>'product_id', f.product_id::text, c.id::text), c.id",
        {"today": now.astimezone(tz).date(), "now": now, "tz": tz.key},
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
            for renewal in sorted({r for c in pending for r in renewal_ids(conn, c["request"])}):
                commands.lock_identity(conn, renewal)  # a resumption may queue it
            billing.load_account(conn, account_id)
            for cmd in pending:
                now = clock.now()  # funds committed before the account lock are visible
                current = commands.get(conn, cmd["id"])
                if current["state"] == "completed" or (
                    current["next_retry_at"] is not None and current["next_retry_at"] > now
                ):
                    continue
                if current["kind"] == "renewal":
                    blocked = _renewal_blocked(conn, current, now.astimezone(tz).date())
                    if blocked == "ended":
                        _retire(conn, current, now)
                        attempted.append(cmd["id"])
                    if blocked is not None:
                        continue
                if current["kind"] == "order":
                    verdict, refused = network.gate(conn, current)
                    if verdict == "wait":
                        continue
                    if refused is not None:
                        network.refuse_order(
                            conn, current, refused, now=now, today=now.astimezone(tz).date()
                        )
                        attempted.append(cmd["id"])
                        continue
                post = (
                    post_charges
                    if "charges" in current["request"]
                    else network.post_profile_change
                    if "profile_changes" in current["request"]
                    else network.post_sim_swap
                    if "sim_swaps" in current["request"]
                    else post_termination
                    if "terminations" in current["request"]
                    else post_offering_change
                    if "offering_changes" in current["request"]
                    else post_status_change
                    if "status_changes" in current["request"]
                    else post_interval
                )
                try:
                    post(conn, cmd["id"], now=now, tz=tz)
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
    attempted += process_transfers(
        conn, clock=clock, tz=tz, checkpoint=checkpoint, heartbeat=heartbeat
    )
    return attempted


_DUE_TRANSFERS = (
    "SELECT c.id, a.id AS action_id, a.billing_account_id AS sender,"
    " a.receiver_billing_account_id AS receiver FROM prepaid_command c"
    " JOIN balance_action a ON a.command_id = c.id"
    " WHERE c.kind = 'transfer' AND c.state <> 'completed' AND a.status = 'created'"
    " AND c.due_date <= %(today)s"
    " AND (c.next_retry_at IS NULL OR c.next_retry_at <= %(now)s) ORDER BY c.created_at, c.id"
)


def process_transfers(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """Complete each due transfer, fee included, in one transaction of its own.

    Lock order is the API's: command identity, then the sender's and the receiver's
    accounts in id order, so opposite transfers (and the API) cannot deadlock.
    """
    ensure_ownership(conn)
    now = clock.now()
    due = fetch_all(conn, _DUE_TRANSFERS, {"today": now.astimezone(tz).date(), "now": now})
    done = []
    for row in due:
        if checkpoint is not None and checkpoint():
            break
        try:
            with conn.transaction():
                commands.lock_identity(conn, row["id"])
                for account_id in sorted({row["sender"], row["receiver"]}):
                    billing.load_account(conn, account_id)
                now = clock.now()
                current = commands.get(conn, row["id"])
                if current["state"] == "completed" or (
                    current["next_retry_at"] is not None and current["next_retry_at"] > now
                ):
                    continue
                with conn.transaction():
                    _complete_transfer(conn, current, row["action_id"], now, tz)
        except Exception as error:
            log.exception("transfer command %s failed", row["id"])
            with conn.transaction():
                commands.lock_identity(conn, row["id"])
                commands.fail(conn, row["id"], str(error) or type(error).__name__, now=clock.now())
        done.append(row["id"])
        if heartbeat is not None:
            heartbeat()
    return done


def _revalidation(conn, action, hold) -> str | None:
    """Why a transfer accepted earlier can no longer be completed (None: it can)."""
    states = {
        r["id"]: r
        for r in fetch_all(
            conn,
            "SELECT id, state::text AS state, currency, rating_type FROM billing_account"
            " WHERE id = ANY(%s)",
            ([action["billing_account_id"], action["receiver_billing_account_id"]],),
        )
    }
    sender, receiver = (
        states[action["billing_account_id"]],
        states[action["receiver_billing_account_id"]],
    )
    if sender["state"] != "Active":
        return f"the sending account is {sender['state']}"
    if receiver["state"] == "Closed":
        return "the receiving account is closed"
    if "prepaid" != sender["rating_type"] or "prepaid" != receiver["rating_type"]:
        return "an account is no longer prepaid"
    if sender["currency"] != receiver["currency"]:
        return "currency: the accounts' currencies differ"
    held = action["amount"] + (
        action["transfer_cost"] if action["cost_owner"] == "originator" else Decimal(0)
    )
    if hold is None or hold["released_at"] is not None or hold["amount"] != held:
        return "the transfer's hold is no longer outstanding"
    return None


def _complete_transfer(conn, cmd, action_id, now, tz) -> None:
    from app.api.transfer import API, RESOURCE, transfer_body

    balance_actions.release_expired(conn, cmd["billing_account_id"], now)
    action = balance_actions.get(conn, action_id, lock=True, kind="transfer")
    if action is None or action["command_id"] != cmd["id"]:
        raise ValueError("transfer command does not match its action")
    if action["status"] != "created":
        raise ValueError("transfer is not awaiting completion")
    hold = fetch_one(conn, "SELECT * FROM funding_hold WHERE id = %s", (action["hold_id"],))
    reason = _revalidation(conn, action, hold)
    sender = action["billing_account_id"]
    if reason is not None:
        funding.release(conn, sender, action["hold_id"], released_at=now, command_id=cmd["id"])
        row = balance_actions.fail_transfer(conn, action["id"], reason, now=now)
        emit.emit(conn, API, RESOURCE, "Failure", lambda: transfer_body(conn, row), at=now)
        commands.complete(conn, cmd["id"], {"outcome": "failed", "reason": reason}, now=now)
        return
    sources = conn.execute(
        "SELECT payment_id, credit_bill_id, funding_source_id, amount FROM funding_hold_source"
        " WHERE hold_id = %s ORDER BY id",
        (action["hold_id"],),
    ).fetchall()
    fee, receiver_pays = action["transfer_cost"], action["cost_owner"] == "receiver"
    held = action["amount"] + (Decimal(0) if receiver_pays else fee)
    if sum((s[3] for s in sources), Decimal(0)) != held:
        raise ValueError("the transfer's hold does not cover its principal and fee")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    funding.release(conn, sender, action["hold_id"], released_at=now, command_id=cmd["id"])
    # The principal leaves the hold's sources first; whatever is left of them pays an
    # originator's fee (a source straddling the boundary is split between the two).
    principal, fee_parts = action["amount"], []
    for payment_id, credit_id, source_id, amount in sources:
        take = min(principal, amount)
        principal -= take
        if take:
            conn.execute(
                "INSERT INTO funding_consumption (billing_account_id, action_id, payment_id,"
                " credit_bill_id, funding_source_id, amount, consumed_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (sender, action["id"], payment_id, credit_id, source_id, take, now),
            )
        if amount > take:
            fee_parts.append((payment_id, credit_id, source_id, amount - take))
    received = conn.execute(
        "INSERT INTO funding_source (kind, billing_account_id, currency, action_id, amount,"
        " available_at) VALUES ('transfer', %s, %s, %s, %s, %s) RETURNING id",
        (action["receiver_billing_account_id"], action["receiver_currency"], action["id"],
         action["amount"], now),
    ).fetchone()[0]  # fmt: skip
    balance_actions.complete_transfer(conn, action["id"], now=now)
    result = {"outcome": "completed", "action_id": str(action["id"])}
    if fee > 0:
        payer = action["receiver_billing_account_id"] if receiver_pays else sender
        result |= transfer_fee.post_fee(
            conn,
            command_id=cmd["id"],
            action_id=action["id"],
            payer_id=payer,
            currency=action["currency"],
            fee=fee,
            parts=[(None, None, received, fee)] if receiver_pays else fee_parts,
            now=now,
            today=now.astimezone(tz).date(),
        )
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    funding.load(conn, sender)
    funding.load(conn, action["receiver_billing_account_id"])
    commands.complete(conn, cmd["id"], result, now=now)


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
