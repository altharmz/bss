"""Legacy funding snapshot, serialized with settlement by the account row lock.

No writes or runtime consumers yet. Call within a transaction; the lock is retained
until it ends. Unissued credits cannot fund purchases and fail reconciliation closed.
"""

from dataclasses import dataclass
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
