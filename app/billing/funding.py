"""Pure source selection for shared prepaid funding; no persistence or API exposure.

Callers must load one account/currency under its account lock. Consumption includes
allocations, payouts and debits; held includes only outstanding reservations.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.billing.model import InconsistentState

ZERO = Decimal("0")
SourceKind = Literal["payment", "creditNote", "adjustment", "transfer", "reversal"]
_ORDER = {kind: rank for rank, kind in enumerate(SourceKind.__args__)}


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    id: UUID
    available_at: datetime
    amount: Decimal
    consumed: Decimal = ZERO
    held: Decimal = ZERO

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.consumed - self.held


@dataclass(frozen=True)
class SourceAmount:
    kind: SourceKind
    id: UUID
    amount: Decimal


class InsufficientFunds(ValueError):
    def __init__(self, required: Decimal, available: Decimal):
        self.required = required
        self.available = available
        super().__init__(f"required {required}, available {available}")


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= ZERO


def _validate(sources: Sequence[Source]) -> None:
    seen = set()
    for source in sources:
        key = (source.kind, source.id)
        if source.kind not in _ORDER or key in seen:
            raise InconsistentState(f"invalid or duplicate funding source {key}")
        seen.add(key)
        if not all(_nonnegative(v) for v in (source.amount, source.consumed, source.held)):
            raise InconsistentState(f"invalid funding amounts for {key}")
        if source.remaining < ZERO:
            raise InconsistentState(f"funding source {key} is overconsumed or overreserved")


def select_sources(sources: Sequence[Source], amount: Decimal) -> tuple[SourceAmount, ...]:
    """Pin a complete amount, preserving payment-before-credit-note settlement order.

    No partial result on a shortfall. This plans a hold/consumption; it does not write it.
    """
    if not _nonnegative(amount):
        raise ValueError("funding amount must be a finite nonnegative Decimal")
    _validate(sources)
    available = sum((source.remaining for source in sources), ZERO)
    if amount > available:
        raise InsufficientFunds(amount, available)
    result = []
    for source in sorted(sources, key=lambda s: (_ORDER[s.kind], s.available_at, s.id)):
        take = min(amount, source.remaining)
        if take:
            result.append(SourceAmount(source.kind, source.id, take))
            amount -= take
        if amount == ZERO:
            break
    return tuple(result)


def reconcile(sources: Sequence[Source], ledger: Decimal, unpaid_obligations: Decimal) -> Decimal:
    """Return spendable ledger balance after reconciling sources and all unpaid charges.

    Obligations include unbilled charges and issued bills' unpaid amounts. Allocations
    reduce source availability and unpaid obligations together, never the ledger twice.
    """
    _validate(sources)
    if not isinstance(ledger, Decimal) or not ledger.is_finite():
        raise InconsistentState("invalid ledger balance")
    if not _nonnegative(unpaid_obligations):
        raise InconsistentState("invalid unpaid obligations")
    unused = sum((s.amount - s.consumed for s in sources), ZERO)
    if unused - unpaid_obligations != ledger:
        raise InconsistentState("funding sources do not reconcile with the ledger")
    return ledger - sum((s.held for s in sources), ZERO)
