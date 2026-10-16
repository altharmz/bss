"""Settlement: which credits pay which customer bills, and the states that follow.

docs/bill-lifecycle-design.md, section 6. Pure like core.py: no database, no clock. The
repository maps rows to these dataclasses (app/repository/billing.py) and writes the
result back under the billing account's row lock.

Credits are payments and the credit of issued credit notes (bills with a negative amount
due, which are `settled` from their issue). Open bills are positive bills in `sent` or
`partiallyPaid`; `new` and `validated` are not issued yet, `onHold` is taken out by the
operator, `settled` has nothing left.

`settle` first applies the explicit items of one payment (TMF676 paymentItem), then the
oldest unused credit to the oldest open bill (by bill date) until one side runs out.
Credits are taken payments first (by received_at), then credit notes (by bill date).
Without explicit items or holds this keeps a simple invariant: there is either no unused
credit or no open bill, so the bills are filled as a prefix and each bill's remaining
amount depends only on the totals - never on when payments arrived relative to runs.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.billing.model import InconsistentState

BillState = Literal["new", "onHold", "validated", "sent", "settled", "partiallyPaid"]
AllocationKind = Literal["automatic", "explicit"]

ZERO = Decimal("0.00")
OPEN: tuple[BillState, ...] = ("sent", "partiallyPaid")


class SettlementError(ValueError):
    """An explicit allocation cannot be applied. Nothing is allocated."""


class UnknownBill(SettlementError):
    """The item names a bill that is not one of the account's."""


class DuplicateBill(SettlementError):
    """Two items name the same bill."""


class BillNotOpen(SettlementError):
    """The bill cannot take a payment now: on hold, settled, not issued, or not positive."""


class ExceedsRemaining(SettlementError):
    """The item's amount is more than the bill's remaining amount."""


class ExceedsPayment(SettlementError):
    """The items together are more than the payment's amount."""


class TransitionRefused(ValueError):
    """The operator asked for a state change the rules do not allow (6.2)."""


@dataclass(frozen=True)
class Bill:
    id: UUID
    bill_date: date
    amount_due: Decimal
    state: BillState
    allocated: Decimal = ZERO  # applied to this bill (positive bills only)
    credit_used: Decimal = ZERO  # applied from this bill (credit notes only)

    @property
    def remaining(self) -> Decimal:
        return max(self.amount_due - self.allocated, ZERO)

    @property
    def is_credit_note(self) -> bool:
        return self.amount_due < 0

    @property
    def credit_left(self) -> Decimal:
        """Unused credit of an issued credit note; zero for anything else."""
        if not self.is_credit_note or self.state != "settled":
            return ZERO
        return -self.amount_due - self.credit_used

    @property
    def is_open(self) -> bool:
        return self.state in OPEN and self.remaining > 0


@dataclass(frozen=True)
class Payment:
    id: UUID
    received_at: datetime
    amount: Decimal
    used: Decimal = ZERO

    @property
    def left(self) -> Decimal:
        return self.amount - self.used


@dataclass(frozen=True)
class ExplicitItem:
    bill_id: UUID
    amount: Decimal


@dataclass(frozen=True)
class Allocation:
    bill_id: UUID
    amount: Decimal
    kind: AllocationKind
    payment_id: UUID | None = None
    credit_bill_id: UUID | None = None


@dataclass(frozen=True)
class StateChange:
    bill_id: UUID
    from_state: BillState
    to_state: BillState


@dataclass(frozen=True)
class Settlement:
    allocations: tuple[Allocation, ...]
    changes: tuple[StateChange, ...]

    @property
    def is_empty(self) -> bool:
        return not self.allocations and not self.changes


def paid_state(amount_due: Decimal, allocated: Decimal) -> BillState:
    """The state an issued positive bill has from its allocations alone."""
    if allocated >= amount_due:
        return "settled"
    return "partiallyPaid" if allocated > 0 else "sent"


def settle(
    bills: Sequence[Bill],
    payments: Sequence[Payment],
    explicit: Sequence[ExplicitItem] = (),
    explicit_payment_id: UUID | None = None,
) -> Settlement:
    """The allocations and state changes that bring one account up to date."""
    by_id = {b.id: b for b in bills}
    pays = {p.id: p for p in payments}
    allocations: list[Allocation] = []

    if explicit:
        if explicit_payment_id not in pays:
            raise ValueError("explicit items need the payment they come from")
        for alloc in _explicit(by_id, pays[explicit_payment_id], explicit):
            allocations.append(alloc)
            by_id[alloc.bill_id] = _add(by_id[alloc.bill_id], alloc.amount)
            pays[explicit_payment_id] = _use(pays[explicit_payment_id], alloc.amount)

    open_bills = sorted((b for b in by_id.values() if b.is_open), key=lambda b: (b.bill_date, b.id))
    credits: list[tuple[str, UUID, Decimal]] = [
        ("payment", p.id, p.left)
        for p in sorted(pays.values(), key=lambda p: (p.received_at, p.id))
        if p.left > 0
    ]
    credits += [
        ("creditBill", b.id, b.credit_left)
        for b in sorted(by_id.values(), key=lambda b: (b.bill_date, b.id))
        if b.credit_left > 0
    ]
    i = j = 0
    while i < len(open_bills) and j < len(credits):
        bill = by_id[open_bills[i].id]
        source, source_id, left = credits[j]
        amount = min(bill.remaining, left)
        allocations.append(
            Allocation(
                bill_id=bill.id,
                amount=amount,
                kind="automatic",
                payment_id=source_id if source == "payment" else None,
                credit_bill_id=source_id if source == "creditBill" else None,
            )
        )
        by_id[bill.id] = _add(bill, amount)
        credits[j] = (source, source_id, left - amount)
        if by_id[bill.id].remaining == 0:
            i += 1
        if credits[j][2] == 0:
            j += 1

    changes = []
    for original in bills:
        if original.state not in OPEN or original.amount_due <= 0:
            continue
        now = paid_state(original.amount_due, by_id[original.id].allocated)
        if now != original.state:
            changes.append(StateChange(original.id, original.state, now))
    return Settlement(tuple(allocations), tuple(changes))


def _explicit(
    bills: dict[UUID, Bill], payment: Payment, items: Iterable[ExplicitItem]
) -> list[Allocation]:
    items = list(items)
    seen: set[UUID] = set()
    for item in items:
        if item.bill_id in seen:
            raise DuplicateBill(f"bill {item.bill_id} appears in more than one item")
        seen.add(item.bill_id)
        bill = bills.get(item.bill_id)
        if bill is None:
            raise UnknownBill(f"bill {item.bill_id} is not a bill of this account")
        if item.amount <= 0:
            raise SettlementError(f"bill {item.bill_id}: the amount must be positive")
        if not bill.is_open:
            raise BillNotOpen(f"bill {bill.id} is {bill.state} and cannot take a payment")
        if item.amount > bill.remaining:
            raise ExceedsRemaining(
                f"bill {bill.id}: {item.amount} is more than its remaining {bill.remaining}"
            )
    total = sum((item.amount for item in items), ZERO)
    if total > payment.left:
        raise ExceedsPayment(f"the items total {total}, more than the payment's {payment.left}")
    return [
        Allocation(bill_id=i.bill_id, amount=i.amount, kind="explicit", payment_id=payment.id)
        for i in items
    ]


def _add(bill: Bill, amount: Decimal) -> Bill:
    return replace(bill, allocated=bill.allocated + amount)


def _use(payment: Payment, amount: Decimal) -> Payment:
    return replace(payment, used=payment.used + amount)


# --- issue: what the billing run does to a bill it has written (6.2) ---

ISSUE_STEPS: tuple[tuple[BillState, BillState], ...] = (("new", "validated"), ("validated", "sent"))


@dataclass(frozen=True)
class IssueCandidate:
    """A bill not issued yet, with what the database says it collected."""

    id: UUID
    bill_date: date
    period_start: date
    period_end: date
    amount_due: Decimal
    state: BillState
    abr_total: Decimal
    abr_count: int
    on_demand: bool = False


def validate(bill: IssueCandidate, period_end: date) -> None:
    """The validation of `validated`: the bill is exactly what the billing rules issue.
    `period_end` is the next cycle day after the bill date. Raises InconsistentState."""
    if bill.abr_count == 0:
        raise InconsistentState(f"bill {bill.id} collects no charge")
    if bill.amount_due != bill.abr_total:
        raise InconsistentState(
            f"bill {bill.id}: amount due {bill.amount_due} is not the sum {bill.abr_total}"
            " of its charges"
        )
    if (bill.period_start, bill.period_end) != (bill.bill_date, period_end):
        raise InconsistentState(
            f"bill {bill.id}: period [{bill.period_start}, {bill.period_end}) is not"
            f" [{bill.bill_date}, {period_end})"
        )


def issue(bill: IssueCandidate) -> tuple[StateChange, ...]:
    """new -> validated -> sent, and -> settled when nothing is due (zero bill, credit
    note). Starts from wherever the bill is, so a bill left `validated` finishes too."""
    if bill.state not in ("new", "validated"):
        raise ValueError(f"bill {bill.id} is {bill.state}, not waiting to be issued")
    changes = []
    state = bill.state
    for from_state, to_state in ISSUE_STEPS:
        if state == from_state:
            changes.append(StateChange(bill.id, from_state, to_state))
            state = to_state
    if bill.amount_due <= 0:
        changes.append(StateChange(bill.id, "sent", "settled"))
    return tuple(changes)


# --- the operator's hold and release (PATCH /customerBill/{id}) ---


def operator_states(bill: Bill) -> tuple[BillState, ...]:
    """The states an operator may set now, besides repeating the current one."""
    if bill.state in OPEN and bill.amount_due > 0:
        return ("onHold",)
    if bill.state == "onHold":
        return (paid_state(bill.amount_due, bill.allocated),)
    return ()


def operator_change(bill: Bill, wanted: BillState) -> StateChange | None:
    """The change an operator's PATCH makes: None for a repeat of the current state,
    TransitionRefused (with the reason) for anything the rules do not allow."""
    if wanted == bill.state:
        return None
    allowed = operator_states(bill)
    if wanted in allowed:
        return StateChange(bill.id, bill.state, wanted)
    if bill.state == "onHold" and wanted in OPEN:
        raise TransitionRefused(
            f"bill {bill.id} is released as {allowed[0]}: its allocations decide, not {wanted}"
        )
    if not allowed:
        raise TransitionRefused(f"bill {bill.id} is {bill.state}: its state cannot be changed")
    raise TransitionRefused(
        f"bill {bill.id} is {bill.state}: it can only be put on hold, not set to {wanted}"
    )
