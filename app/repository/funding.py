"""Legacy funding snapshot, serialized with settlement by the account row lock.

No runtime consumers yet. Call within a transaction; the lock is retained
until it ends. Unissued credits cannot fund purchases and fail reconciliation closed.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg

from app.billing.funding import InsufficientFunds, Source, SourceAmount, reconcile, select_sources
from app.repository import billing


@dataclass(frozen=True)
class Snapshot:
    account_id: UUID
    currency: str
    sources: tuple[Source, ...]
    ledger: Decimal
    unpaid_obligations: Decimal
    spendable: Decimal


@dataclass(frozen=True)
class Plan:
    snapshot: Snapshot
    amount: Decimal
    sources: tuple[SourceAmount, ...]


def plan(conn: psycopg.Connection, account_id: UUID, amount: Decimal) -> Plan:
    """Quote exact sources under the account lock, respecting unpaid obligations.

    This does not reserve money. A future writer must persist the hold/consumption
    in this same transaction before releasing the account lock. Repeated calls do
    not accumulate reservations. Zero is allowed even on an indebted account.
    """
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
        raise ValueError("funding amount must be a finite nonnegative Decimal")
    snapshot = load(conn, account_id)
    available = max(Decimal(0), snapshot.spendable)
    if amount > available:
        raise InsufficientFunds(amount, available)
    return Plan(snapshot, amount, select_sources(snapshot.sources, amount))


def load(conn: psycopg.Connection, account_id: UUID) -> Snapshot:
    """Load and reconcile existing payment/credit-note funding without changing it.

    Obligations include held and unissued positive bills as well as unbilled ABRs.
    Allocations reduce obligations and sources together; payouts reduce sources and
    the ledger together. Neither is subtracted twice.
    """
    account = billing.load_account(conn, account_id)
    bills, payments = billing.load_settlement(conn, account_id)
    sources = tuple(
        Source("payment", p.id, p.received_at, p.amount, p.used) for p in payments
    ) + tuple(
        Source(
            "creditNote",
            b.id,
            datetime.combine(b.bill_date, time.min, UTC),
            -b.amount_due,
            b.credit_used,
        )
        for b in bills
        if b.is_credit_note and b.state == "settled"
    )
    held = {}
    for payment_id, credit_id, amount in conn.execute(
        "SELECT s.payment_id, s.credit_bill_id, sum(s.amount)"
        " FROM funding_hold_source s JOIN funding_hold h ON h.id = s.hold_id"
        " WHERE h.billing_account_id = %s AND h.released_at IS NULL"
        " GROUP BY s.payment_id, s.credit_bill_id",
        (account_id,),
    ):
        held[("payment", payment_id) if payment_id else ("creditNote", credit_id)] = amount
    sources = tuple(replace(s, held=held.get((s.kind, s.id), Decimal(0))) for s in sources)
    unbilled = conn.execute(
        "SELECT coalesce(sum(amount + tax_amount), 0)"
        " FROM applied_customer_billing_rate"
        " WHERE billing_account_id = %s AND customer_bill_id IS NULL",
        (account_id,),
    ).fetchone()[0]
    obligations = unbilled + sum(
        (b.amount_due - b.allocated for b in bills if b.amount_due > 0), Decimal(0)
    )
    ledger = billing.account_balance(conn, account_id)
    spendable = reconcile(sources, ledger, obligations)
    return Snapshot(account_id, account.currency, sources, ledger, obligations, spendable)


def reserve(
    conn: psycopg.Connection, account_id: UUID, amount: Decimal, *, held_at: datetime
) -> UUID:
    """Pin a fresh hold atomically with its quote; caller owns the transaction.

    This internal foundation has no API caller or retry identity yet. Do not use it
    for worker commands until their durable ownership/idempotency journal exists.
    """
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount < 0
        or amount > Decimal("9999999999.99")
        or amount != amount.quantize(Decimal("0.01"))
    ):
        raise ValueError("hold amount must be nonnegative exact cents within numeric(12,2)")
    result = plan(conn, account_id, amount)
    hold_id = conn.execute(
        "INSERT INTO funding_hold (billing_account_id, currency, amount, held_at)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (account_id, result.snapshot.currency, amount, held_at),
    ).fetchone()[0]
    for source in result.sources:
        conn.execute(
            "INSERT INTO funding_hold_source"
            " (hold_id, billing_account_id, payment_id, credit_bill_id, amount)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                hold_id,
                account_id,
                source.id if source.kind == "payment" else None,
                source.id if source.kind == "creditNote" else None,
                source.amount,
            ),
        )
    conn.execute("SET CONSTRAINTS funding_hold_bounds IMMEDIATE")
    conn.execute("SET CONSTRAINTS funding_hold_bounds DEFERRED")
    return hold_id


def release(
    conn: psycopg.Connection,
    account_id: UUID,
    hold_id: UUID,
    *,
    released_at: datetime,
    command_id: UUID | None = None,
) -> None:
    """Release once under the owner lock; repeated release retains the first instant."""
    billing.load_account(conn, account_id)
    row = conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s AND billing_account_id = %s",
        (hold_id, account_id),
    ).fetchone()
    if row is None:
        raise ValueError("unknown funding hold for account")
    owner = conn.execute(
        "SELECT command_id FROM funding_command_hold WHERE hold_id = %s", (hold_id,)
    ).fetchone()
    if (owner[0] if owner else None) != command_id:
        raise ValueError("funding hold command owner mismatch")
    if row[0] is None:
        conn.execute(
            "UPDATE funding_hold SET released_at = %s WHERE id = %s", (released_at, hold_id)
        )


def reserve_for_command(
    conn: psycopg.Connection,
    account_id: UUID,
    amount: Decimal,
    *,
    command_id: UUID,
    held_at: datetime,
) -> UUID:
    """Reserve once for an internal command, atomically with its retained identity.

    Acquire identity before account locks. Callers must not already hold account or
    child locks. Same account/amount replays the original hold even after release;
    a changed quote instant does not change the request. A rollback consumes no key.
    This is not the HTTP idempotency journal or the worker command/result journal.
    """
    if not isinstance(command_id, UUID):
        raise ValueError("command identity must be a UUID")
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
        raise ValueError("funding amount must be a finite nonnegative Decimal")
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"funding-command:{command_id}",),
    )
    existing = conn.execute(
        "SELECT h.id, h.billing_account_id, h.amount FROM funding_command_hold c"
        " JOIN funding_hold h ON h.id = c.hold_id WHERE c.command_id = %s",
        (command_id,),
    ).fetchone()
    if existing is not None:
        if existing[1] != account_id or existing[2] != amount:
            raise ValueError("funding command request mismatch")
        return existing[0]
    hold_id = reserve(conn, account_id, amount, held_at=held_at)
    conn.execute(
        "INSERT INTO funding_command_hold (command_id, hold_id, billing_account_id)"
        " VALUES (%s, %s, %s)",
        (command_id, hold_id, account_id),
    )
    return hold_id
