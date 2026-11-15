"""The events customers are told about, read from durable facts.

Nothing hooks into the code that makes an event: each detector reads the rows that code
already leaves (usage thresholds, lifecycle orders, declined charges, port requests, paid
intervals, balances) and returns an `Event` whose `key` identifies it, so a tick that runs
twice, or a worker that was down, queues each message exactly once (the key is unique with the
channel). History is not replayed after a deploy: facts older than `notify_lookback_days` are
ignored; state-based events (low balance, plan expiring, grace) describe now.
docs/decisions/notifications.md.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import entitlement
from app.billing import lifecycle as plan
from app.billing.lifecycle import funded_state, is_payg
from app.config import settings
from app.notify import recipients
from app.notify.service import Event
from app.repository import billing, funding
from app.repository import lifecycle as lifecycle_repo
from app.repository import security as security_repo
from app.repository._sql import Row, fetch_all, fetch_one

ALLOWANCE = {"data": "data allowance", "voice": "voice minutes", "sms": "text messages"}


def money(amount: Decimal, currency: str) -> str:
    return f"{amount:.2f} {currency}"


def day(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def line(conn: psycopg.Connection, product_id: UUID | None) -> str:
    number = None if product_id is None else recipients.line_number(conn, product_id)
    return f"+{number}" if number else "your line"


def lines(conn: psycopg.Connection, product_ids: list[UUID | None]) -> Callable[[UUID | None], str]:
    """`line` for many products, read in one statement (#305): the detectors that look back
    over every line find the same events on every tick."""
    numbers = recipients.line_numbers(conn, [p for p in product_ids if p is not None])
    return lambda product_id: f"+{numbers[product_id]}" if product_id in numbers else "your line"


def detectors(
    now: datetime, tz: ZoneInfo, accounts: list[UUID] | None = None
) -> list[tuple[str, Callable[[psycopg.Connection], list[Event]]]]:
    """The detectors, named, so one that fails does not stop the others. `accounts` limits
    the per-account detector to those accounts (app/repository/account_review.py)."""
    today = now.astimezone(tz).date()
    since = now - timedelta(days=settings.notify_lookback_days)
    return [
        ("accounts", lambda conn: _accounts(conn, today, accounts)),
        ("plan_expiring", lambda conn: _plan_expiring(conn, today)),
        ("renewals", lambda conn: _renewals(conn, since)),
        ("lifecycle", lambda conn: _lifecycle(conn, since)),
        ("usage", lambda conn: _usage(conn, since)),
        ("card_declines", lambda conn: _card_declines(conn, since)),
        ("ports", lambda conn: _ports(conn, since)),
        ("sim_swaps", lambda conn: _sim_swaps(conn, since)),
        ("security", lambda conn: _security(conn, since)),
    ]


def detect(conn: psycopg.Connection, now: datetime, tz: ZoneInfo) -> list[Event]:
    return [event for _, run in detectors(now, tz) for event in run(conn)]


def _account_ids(conn: psycopg.Connection, only: list[UUID] | None) -> list[UUID]:
    query = (
        "SELECT DISTINCT a.id FROM billing_account a JOIN product p"
        " ON p.billing_account_id = a.id WHERE a.rating_type = 'prepaid'"
        " AND a.state = 'Active' AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
        " AND p.termination_date IS NULL AND p.parent_product_id IS NULL"
    )
    if only is None:
        return [row["id"] for row in fetch_all(conn, query + " ORDER BY a.id")]
    return [
        row["id"] for row in fetch_all(conn, query + " AND a.id = ANY(%s) ORDER BY a.id", (only,))
    ]


def _accounts(conn: psycopg.Connection, today: date, only: list[UUID] | None) -> list[Event]:
    """Low balance and grace: both need the account's funding, read under its lock."""
    events: list[Event] = []
    ids = _account_ids(conn, only)
    if not ids:
        return events
    # once per pass (#305): a change of the settings applies from the next pass
    lifecycle_settings = lifecycle_repo.settings(conn)
    for account_id in ids:
        with conn.transaction():
            account = billing.load_account_row(conn, account_id)
            spendable = funding.load(conn, account_id, locked=account).spendable
            events += _low_balance(conn, account, spendable)
            events += _grace(conn, account, spendable, today, lifecycle_settings)
    return events


def _low_balance(conn: psycopg.Connection, account: Row, spendable: Decimal) -> list[Event]:
    threshold = settings.notify_low_balance
    if threshold <= 0 or spendable >= threshold:
        return []
    last = fetch_one(
        conn,
        "SELECT id::text AS id FROM (SELECT id, confirmed_at AS at FROM balance_action"
        " WHERE kind = 'topup' AND status = 'completed' AND billing_account_id = %(a)s"
        " UNION ALL SELECT id, received_at FROM payment"
        " WHERE billing_account_id = %(a)s AND status = 'done') x"
        " ORDER BY at DESC NULLS LAST LIMIT 1",
        {"a": account["id"]},
    )
    currency = account["currency"]
    return [
        Event(
            "low_balance",
            f"low_balance:{account['id']}:{last['id'] if last else 'start'}",
            account["id"],
            None,
            {
                "balance": money(spendable, currency),
                "threshold": money(threshold, currency),
            },
        )
    ]


def _grace(
    conn: psycopg.Connection,
    account: Row,
    spendable: Decimal,
    today: date,
    settings_row: plan.Settings,
) -> list[Event]:
    rows = fetch_all(
        conn,
        "SELECT p.* FROM product p WHERE p.billing_account_id = %s AND p.parent_product_id IS NULL"
        " AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
        " AND p.termination_date IS NULL ORDER BY p.id",
        (account["id"],),
    )
    events = []
    for product in rows:
        if product["manually_suspended"] and not product["lifecycle_suspended"]:
            continue  # an operator's pause
        if product["lifecycle_suspended"]:
            continue  # past grace: the suspension message covers it
        funded, since = funded_state(
            conn, product, account, spendable if is_payg(product) else None, settings_row, today
        )
        if funded or since is None or today < since:
            continue
        due = plan.schedule(since, settings_row)
        events.append(
            Event(
                "grace",
                f"grace:{product['id']}:{since}",
                account["id"],
                product["id"],
                {
                    "plan": product["name"],
                    "date": day(due.suspend_on),
                    "line": line(conn, product["id"]),
                },
            )
        )
    return events


# The latest grants ending in the window: the grants ending in it (the upper(period) index)
# that no later grant follows, not the latest grant of every product (#311).
_PLAN_EXPIRING = (
    "SELECT g.id, g.product_id, g.billing_account_id, upper(g.period) AS due, p.name"  # noqa: S608 - LATER_GRANT is code
    " FROM funded_interval g JOIN product p ON p.id = g.product_id JOIN billing_account a"
    " ON a.id = g.billing_account_id"
    " WHERE upper(g.period) > %(today)s AND upper(g.period) <= %(limit)s"
    " AND NOT EXISTS (" + entitlement.LATER_GRANT + ")"
    " AND a.rating_type = 'prepaid' AND a.state = 'Active'"
    " AND NOT p.grantless AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
    " AND p.termination_date IS NULL"
    " AND NOT (p.manually_suspended AND NOT p.lifecycle_suspended) ORDER BY g.id"
)


def _plan_expiring(conn: psycopg.Connection, today: date) -> list[Event]:
    rows = fetch_all(
        conn,
        _PLAN_EXPIRING,
        {"today": today, "limit": today + timedelta(days=settings.notify_expiring_days)},
    )
    return [
        Event(
            "plan_expiring",
            f"plan_expiring:{row['id']}",
            row["billing_account_id"],
            row["product_id"],
            {
                "plan": row["name"],
                "days": str((row["due"] - today).days),
                "date": day(row["due"]),
            },
        )
        for row in rows
    ]


def _renewals(conn: psycopg.Connection, since: datetime) -> list[Event]:
    events = []
    done = fetch_all(
        conn,
        "SELECT c.id, c.billing_account_id, f.product_id, p.name FROM prepaid_command c"
        " JOIN funded_interval f ON f.id = (c.request->>'previous_interval_id')::uuid"
        " JOIN product p ON p.id = f.product_id WHERE c.kind = 'renewal'"
        " AND c.state = 'completed' AND c.result->>'outcome' = 'funded'"
        " AND c.attempted_at >= %s ORDER BY c.attempted_at, c.id",
        (since,),
    )
    grants = entitlement.latest_grants(conn, [row["product_id"] for row in done])
    for row in done:
        grant = grants.get(row["product_id"])
        through = grant["period"].upper if grant else None
        events.append(
            Event(
                "renewal_done",
                f"renewal_done:{row['id']}",
                row["billing_account_id"],
                row["product_id"],
                {"plan": row["name"], "date": "" if through is None else day(through)},
            )
        )
    failed = fetch_all(
        conn,
        "SELECT DISTINCT ON (c.id) c.id, c.billing_account_id, f.product_id, p.name,"
        " s.required - s.available AS shortfall, a.currency FROM prepaid_funding_shortfall s"
        " JOIN prepaid_command c ON c.id = s.command_id"
        " JOIN funded_interval f ON f.id = (c.request->>'previous_interval_id')::uuid"
        " JOIN product p ON p.id = f.product_id JOIN billing_account a"
        " ON a.id = c.billing_account_id WHERE c.kind = 'renewal' AND c.state = 'pending'"
        " AND s.observed_at >= %s ORDER BY c.id, s.observed_at DESC",
        (since,),
    )
    for row in failed:
        events.append(
            Event(
                "renewal_failed",
                f"renewal_failed:{row['id']}",
                row["billing_account_id"],
                row["product_id"],
                {"plan": row["name"], "shortfall": money(row["shortfall"], row["currency"])},
            )
        )
    return events


def _lifecycle(conn: psycopg.Connection, since: datetime) -> list[Event]:
    """A suspension or deactivation counts once the order that made it completed (the network
    confirmed it). A suspended line is deactivated `suspensionDays` after its suspension."""
    rows = fetch_all(
        conn,
        "SELECT e.id, e.action, e.business_date, e.product_id, e.billing_account_id, p.name FROM"
        " prepaid_lifecycle_event e JOIN product_order o ON o.id = e.product_order_id"
        " JOIN product p ON p.id = e.product_id WHERE e.outcome = 'placed'"
        " AND e.action IN ('suspend', 'deactivate') AND o.state = 'completed'"
        " AND o.completion_date >= %s ORDER BY o.completion_date, e.id",
        (since,),
    )
    if not rows:
        return []
    suspension_days = lifecycle_repo.settings(conn).suspension_days
    line_of = lines(conn, [row["product_id"] for row in rows])
    events = []
    for row in rows:
        suspended = row["action"] == "suspend"
        deactivation = row["business_date"] + timedelta(days=suspension_days)
        events.append(
            Event(
                "suspended" if suspended else "deactivated",
                f"lifecycle:{row['id']}",
                row["billing_account_id"],
                row["product_id"],
                {
                    "plan": row["name"],
                    "line": line_of(row["product_id"]),
                    "date": day(deactivation),
                },
                sms_to_line=suspended,
            )
        )
    return events


def _usage(conn: psycopg.Connection, since: datetime) -> list[Event]:
    rows = fetch_all(
        conn,
        "SELECT t.*, p.billing_account_id FROM usage_threshold t JOIN product p"
        " ON p.id = t.product_id WHERE t.reached_at >= %s ORDER BY t.reached_at, t.threshold",
        (since,),
    )
    line_of = lines(conn, [row["product_id"] for row in rows])
    return [
        Event(
            f"usage_{row['threshold']}",
            f"usage:{row['product_id']}:{row['period_start']}:{row['allowance']}:{row['threshold']}",
            row["billing_account_id"],
            row["product_id"],
            {"allowance": ALLOWANCE[row["allowance"]], "line": line_of(row["product_id"])},
        )
        for row in rows
    ]


def _card_declines(conn: psycopg.Connection, since: datetime) -> list[Event]:
    rows = fetch_all(
        conn,
        "SELECT c.id, c.billing_account_id, c.decline_reason, m.last_four"
        " FROM payment_command c JOIN payment_method m ON m.id = c.payment_method_id"
        " WHERE c.state = 'declined' AND c.created_at >= %s ORDER BY c.created_at, c.id",
        (since,),
    )
    return [
        Event(
            "card_declined",
            f"card_declined:{row['id']}",
            row["billing_account_id"],
            None,
            {
                "last4": row["last_four"] or "????",
                "reason": row["decline_reason"] or "declined by the bank",
            },
        )
        for row in rows
    ]


def _ports(conn: psycopg.Connection, since: datetime) -> list[Event]:
    """Port-in status for the line that claims the ported number (a request nobody claimed
    yet has no customer: it is picked up once the order claims the number)."""
    rows = fetch_all(
        conn,
        "SELECT r.id, r.state, r.submissions, r.number_value, r.rejection_reason, a.product_id,"
        " p.billing_account_id FROM port_request r JOIN LATERAL ("
        "  SELECT product_id FROM number_assignment WHERE subscriber_number_id ="
        "  r.subscriber_number_id AND cancelled_at IS NULL ORDER BY effective_from DESC LIMIT 1)"
        " a ON true JOIN product p ON p.id = a.product_id"
        " WHERE r.direction = 'in' AND r.state IN ('pending', 'rejected', 'completed')"
        " AND r.updated_at >= %s ORDER BY r.updated_at, r.id",
        (since,),
    )
    return [
        Event(
            f"port_in_{row['state']}",
            f"port_in:{row['id']}:{row['submissions']}:{row['state']}",
            row["billing_account_id"],
            row["product_id"],
            {
                "line": f"+{row['number_value']}",
                "reason": row["rejection_reason"] or "no reason given",
            },
        )
        for row in rows
    ]


def _sim_swaps(conn: psycopg.Connection, since: datetime) -> list[Event]:
    rows = fetch_all(
        conn,
        "SELECT c.id, c.product_id, p.billing_account_id FROM network_command c JOIN product p"
        " ON p.id = c.product_id WHERE c.kind = 'swap_sim' AND c.state = 'confirmed'"
        " AND c.confirmed_at >= %s ORDER BY c.confirmed_at, c.id",
        (since,),
    )
    line_of = lines(conn, [row["product_id"] for row in rows])
    return [
        Event(
            "sim_swap",
            f"sim_swap:{row['id']}",
            row["billing_account_id"],
            row["product_id"],
            {"line": line_of(row["product_id"])},
        )
        for row in rows
    ]


def _security(conn: psycopg.Connection, since: datetime) -> list[Event]:
    """Every sensitive action that was done (and a port-out a lock stopped) is told to the
    customer, once per audit entry. A contact change goes to the addresses on record before
    the change (kept in the entry), so it cannot redirect its own alert."""
    events = []
    audit = security_repo.audit_since(conn, since)
    line_of = lines(conn, [row["product_id"] for row in audit])
    for row in audit:
        kind = row["action"]
        if kind == "port_out" and row["outcome"] == "refused":
            kind = "port_out_blocked"
        customer_id = row["customer_id"]
        contact = None if customer_id is None else recipients.for_customer(conn, customer_id)
        before = (row["detail"] or {}).get("alertTo") or {}
        if contact is not None and (before.get("phones") or before.get("emails")):
            contact = recipients.Contact(
                contact.customer_id,
                contact.name,
                before.get("emails", []),
                before.get("phones", []),
            )
        account_id = row["billing_account_id"] or (
            None if customer_id is None else security_repo.first_account(conn, customer_id)
        )
        if contact is None and account_id is None:
            continue
        events.append(
            Event(
                f"security_{kind}",
                f"security:{row['id']}",
                account_id,
                row["product_id"],
                {"line": line_of(row["product_id"])},
                contact=contact,
                urgent=True,
            )
        )
    return events
