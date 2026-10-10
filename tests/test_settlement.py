"""The pure settlement (app/billing/settlement.py): which credits pay which bills.

Hand-checked examples first, then properties over random histories of bills, payments
and holds. The oracle is the cumulative rule of docs/bill-lifecycle-design.md 3.0: with
no holds and no explicit items, bill k (positive bills in bill-date order, cumulative
dues cum_k) has remaining min(a_k, max(0, cum_k - C)), C = all payments + all issued
credit notes - whatever order payments and bills arrived in, and whether settlement ran
after every payment or only in billing runs."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import count
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.billing.model import InconsistentState
from app.billing.settlement import (
    Bill,
    BillNotOpen,
    DuplicateBill,
    ExceedsPayment,
    ExceedsRemaining,
    ExplicitItem,
    IssueCandidate,
    Payment,
    SettlementError,
    StateChange,
    TransitionRefused,
    UnknownBill,
    issue,
    operator_change,
    operator_states,
    paid_state,
    settle,
    validate,
)

D = Decimal
ZERO = D("0.00")
_ids = count(1)


def uid() -> UUID:
    return UUID(int=next(_ids))


def bill(amount: str, day: int = 1, state: str | None = None, allocated: str = "0") -> Bill:
    due = D(amount)
    return Bill(
        id=uid(),
        bill_date=date(2026, 1, 1) + timedelta(days=day),
        amount_due=due,
        state=state or ("settled" if due <= 0 else "sent"),
        allocated=D(allocated),
    )


def pay(amount: str, minute: int = 0, used: str = "0") -> Payment:
    return Payment(
        id=uid(),
        received_at=datetime(2026, 1, 1) + timedelta(minutes=minute),
        amount=D(amount),
        used=D(used),
    )


# --- examples ---


def test_the_worked_example_of_the_design():
    """3.0: 200.00 paid before the 464.52 bill, then 264.52."""
    first = pay("200.00")
    nov = bill("464.52", day=31)
    result = settle([nov], [first])
    [alloc] = result.allocations
    assert (alloc.bill_id, alloc.amount, alloc.payment_id, alloc.kind) == (
        nov.id,
        D("200.00"),
        first.id,
        "automatic",
    )
    assert result.changes == (StateChange(nov.id, "sent", "partiallyPaid"),)

    nov = replace(nov, state="partiallyPaid", allocated=D("200.00"))
    second = pay("264.52", minute=5)
    result = settle([nov], [replace(first, used=D("200.00")), second])
    assert [(a.amount, a.payment_id) for a in result.allocations] == [(D("264.52"), second.id)]
    assert result.changes == (StateChange(nov.id, "partiallyPaid", "settled"),)


def test_oldest_bill_first_and_one_payment_split_over_two_bills():
    old, new = bill("100.00", day=1), bill("100.00", day=31)
    p = pay("150.00")
    result = settle([new, old], [p])  # input order does not matter
    assert [(a.bill_id, a.amount) for a in result.allocations] == [
        (old.id, D("100.00")),
        (new.id, D("50.00")),
    ]
    assert set(result.changes) == {
        StateChange(old.id, "sent", "settled"),
        StateChange(new.id, "sent", "partiallyPaid"),
    }


def test_older_payment_is_used_first():
    b = bill("100.00")
    late, early = pay("80.00", minute=10), pay("80.00", minute=1)
    result = settle([b], [late, early])
    assert [(a.payment_id, a.amount) for a in result.allocations] == [
        (early.id, D("80.00")),
        (late.id, D("20.00")),
    ]


def test_a_credit_note_pays_an_older_open_bill_after_the_payments():
    old = bill("100.00", day=1)
    note = bill("-30.00", day=31)  # issued: settled
    p = pay("50.00")
    result = settle([old, note], [p])
    assert [(a.amount, a.payment_id, a.credit_bill_id) for a in result.allocations] == [
        (D("50.00"), p.id, None),
        (D("30.00"), None, note.id),
    ]
    assert result.changes == (StateChange(old.id, "sent", "partiallyPaid"),)


def test_credit_note_not_issued_yet_is_not_credit():
    old = bill("100.00")
    assert settle([old, bill("-30.00", day=31, state="new")], []).is_empty


@pytest.mark.parametrize("state", ["new", "validated", "onHold", "settled"])
def test_only_issued_open_bills_take_automatic_payments(state):
    b = bill("100.00", state=state, allocated="100.00" if state == "settled" else "0")
    assert settle([b], [pay("500.00")]).is_empty


def test_payment_larger_than_all_bills_leaves_credit_for_the_next_bill():
    p = pay("500.00")
    first = bill("100.00", day=1)
    result = settle([first], [p])
    assert [a.amount for a in result.allocations] == [D("100.00")]
    second = bill("300.00", day=31)
    result = settle(
        [replace(first, state="settled", allocated=D("100.00")), second],
        [replace(p, used=D("100.00"))],
    )
    assert [(a.bill_id, a.amount) for a in result.allocations] == [(second.id, D("300.00"))]
    assert result.changes == (StateChange(second.id, "sent", "settled"),)


def test_nothing_to_do_is_empty():
    assert settle([], []).is_empty
    assert settle([bill("100.00")], []).is_empty
    assert settle([], [pay("10.00")]).is_empty


# --- explicit items (TMF676 paymentItem) ---


def test_explicit_items_go_first_and_the_rest_oldest_first():
    old, mid, new = bill("100.00", day=1), bill("100.00", day=31), bill("100.00", day=61)
    p = pay("150.00")
    result = settle([old, mid, new], [p], [ExplicitItem(new.id, D("100.00"))], p.id)
    assert [(a.bill_id, a.amount, a.kind) for a in result.allocations] == [
        (new.id, D("100.00"), "explicit"),
        (old.id, D("50.00"), "automatic"),
    ]
    assert set(result.changes) == {
        StateChange(new.id, "sent", "settled"),
        StateChange(old.id, "sent", "partiallyPaid"),
    }


@pytest.mark.parametrize(
    ("make_items", "error"),
    [
        (lambda b, h: [ExplicitItem(uid(), D("1.00"))], UnknownBill),
        (
            lambda b, h: [ExplicitItem(b.id, D("1.00")), ExplicitItem(b.id, D("1.00"))],
            DuplicateBill,
        ),
        (lambda b, h: [ExplicitItem(b.id, D("100.01"))], ExceedsRemaining),
        (lambda b, h: [ExplicitItem(b.id, D("0.00"))], SettlementError),
        (lambda b, h: [ExplicitItem(h.id, D("1.00"))], BillNotOpen),
    ],
)
def test_invalid_explicit_items_are_refused(make_items, error):
    b, held = bill("100.00"), bill("50.00", day=31, state="onHold")
    p = pay("500.00")
    with pytest.raises(error):
        settle([b, held], [p], make_items(b, held), p.id)


def test_explicit_items_above_the_payment_are_refused():
    a, b = bill("100.00"), bill("100.00", day=31)
    p = pay("150.00")
    with pytest.raises(ExceedsPayment):
        settle([a, b], [p], [ExplicitItem(a.id, D("100.00")), ExplicitItem(b.id, D("60.00"))], p.id)


def test_explicit_items_to_a_settled_or_negative_bill_are_refused():
    paid = bill("100.00", state="settled", allocated="100.00")
    note = bill("-10.00", day=31)
    p = pay("10.00")
    for target in (paid, note):
        with pytest.raises(BillNotOpen):
            settle([paid, note], [p], [ExplicitItem(target.id, D("1.00"))], p.id)


# --- the operator's hold and release ---


@pytest.mark.parametrize(
    ("state", "allocated", "allowed"),
    [
        ("sent", "0", ("onHold",)),
        ("partiallyPaid", "40.00", ("onHold",)),
        ("onHold", "0", ("sent",)),
        ("onHold", "40.00", ("partiallyPaid",)),
        ("settled", "100.00", ()),
        ("new", "0", ()),
        ("validated", "0", ()),
    ],
)
def test_operator_states(state, allocated, allowed):
    assert operator_states(bill("100.00", state=state, allocated=allocated)) == allowed


def test_operator_change_follows_the_rules():
    sent = bill("100.00")
    assert operator_change(sent, "sent") is None  # a repeat is not a change
    assert operator_change(sent, "onHold") == StateChange(sent.id, "sent", "onHold")
    held = replace(sent, state="onHold", allocated=D("40.00"))
    assert operator_change(held, "partiallyPaid") == StateChange(sent.id, "onHold", "partiallyPaid")
    with pytest.raises(TransitionRefused, match="released as partiallyPaid"):
        operator_change(held, "sent")
    with pytest.raises(TransitionRefused, match="only be put on hold"):
        operator_change(sent, "settled")
    with pytest.raises(TransitionRefused, match="cannot be changed"):
        operator_change(replace(sent, state="settled", allocated=D("100.00")), "onHold")
    with pytest.raises(TransitionRefused, match="cannot be changed"):
        operator_change(bill("-5.00"), "onHold")  # a credit note is settled at issue


# --- issue: what the run does to a bill it wrote ---


def candidate(amount: str = "300.00", state: str = "new", **over) -> IssueCandidate:
    fields = {
        "id": uid(),
        "bill_date": date(2026, 12, 1),
        "period_start": date(2026, 12, 1),
        "period_end": date(2027, 1, 1),
        "amount_due": D(amount),
        "state": state,
        "abr_total": D(amount),
        "abr_count": 1,
    }
    return IssueCandidate(**{**fields, **over})


@pytest.mark.parametrize(
    ("amount", "state", "steps"),
    [
        ("300.00", "new", [("new", "validated"), ("validated", "sent")]),
        ("300.00", "validated", [("validated", "sent")]),
        ("0.00", "new", [("new", "validated"), ("validated", "sent"), ("sent", "settled")]),
        ("-116.13", "new", [("new", "validated"), ("validated", "sent"), ("sent", "settled")]),
    ],
)
def test_issue_steps(amount, state, steps):
    c = candidate(amount, state)
    assert [(s.from_state, s.to_state) for s in issue(c)] == steps
    assert {s.bill_id for s in issue(c)} == {c.id}


def test_only_a_bill_waiting_to_be_issued_is_issued():
    with pytest.raises(ValueError, match="not waiting"):
        issue(candidate(state="sent"))


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"abr_count": 0, "abr_total": D("0")}, "no charge"),
        ({"abr_total": D("299.99")}, "not the sum"),
        ({"period_end": date(2026, 12, 31)}, "period"),
        ({"period_start": date(2026, 11, 30)}, "period"),
    ],
)
def test_validation(over, match):
    validate(candidate(), date(2027, 1, 1))
    with pytest.raises(InconsistentState, match=match):
        validate(candidate(**over), date(2027, 1, 1))


def test_paid_state():
    assert [paid_state(D("10.00"), D(x)) for x in ("0", "0.01", "9.99", "10.00")] == [
        "sent",
        "partiallyPaid",
        "partiallyPaid",
        "settled",
    ]


# --- properties over random histories ---


@dataclass
class Book:
    """An account's bills and payments with settlement applied as the database would."""

    bills: dict[UUID, Bill] = field(default_factory=dict)
    payments: dict[UUID, Payment] = field(default_factory=dict)
    history: dict[UUID, list[str]] = field(default_factory=dict)
    days: int = 0
    minutes: int = 0

    def add_bill(self, cents: int) -> Bill:
        self.days += 30
        b = bill(str(D(cents) / 100), day=self.days)
        self.bills[b.id] = b
        self.history[b.id] = [b.state]
        return b

    def add_payment(self, cents: int) -> Payment:
        self.minutes += 1
        p = pay(str(D(cents) / 100), minute=self.minutes)
        self.payments[p.id] = p
        return p

    def settle(self, explicit=(), explicit_payment_id=None):
        result = settle(
            list(self.bills.values()), list(self.payments.values()), explicit, explicit_payment_id
        )
        for a in result.allocations:
            b = self.bills[a.bill_id]
            self.bills[a.bill_id] = replace(b, allocated=b.allocated + a.amount)
            if a.payment_id:
                p = self.payments[a.payment_id]
                self.payments[a.payment_id] = replace(p, used=p.used + a.amount)
            else:
                n = self.bills[a.credit_bill_id]
                self.bills[a.credit_bill_id] = replace(n, credit_used=n.credit_used + a.amount)
        for c in result.changes:
            assert self.bills[c.bill_id].state == c.from_state
            self.bills[c.bill_id] = replace(self.bills[c.bill_id], state=c.to_state)
            self.history[c.bill_id].append(c.to_state)
        return result

    def set_state(self, bill_id: UUID, wanted: str) -> None:
        change = operator_change(self.bills[bill_id], wanted)
        if change:
            self.bills[bill_id] = replace(self.bills[bill_id], state=change.to_state)
            self.history[bill_id].append(change.to_state)

    def check_bounds(self) -> None:
        for b in self.bills.values():
            assert ZERO <= b.allocated <= max(b.amount_due, ZERO)
            assert ZERO <= b.credit_used <= max(-b.amount_due, ZERO)
            if b.amount_due > 0 and b.state not in ("onHold",):
                assert b.state == paid_state(b.amount_due, b.allocated), b
        for p in self.payments.values():
            assert ZERO <= p.used <= p.amount

    def oracle(self) -> dict[UUID, Decimal]:
        """Remaining per positive bill by the cumulative rule of 3.0."""
        credit = sum((p.amount for p in self.payments.values()), ZERO) + sum(
            (-b.amount_due for b in self.bills.values() if b.amount_due < 0), ZERO
        )
        out, cum = {}, ZERO
        for b in sorted(self.bills.values(), key=lambda b: b.bill_date):
            if b.amount_due > 0:
                cum += b.amount_due
                out[b.id] = min(b.amount_due, max(ZERO, cum - credit))
        return out


cents = st.integers(min_value=-5_000, max_value=30_000)
payment_cents = st.integers(min_value=1, max_value=40_000)
events = st.lists(
    st.one_of(st.tuples(st.just("bill"), cents), st.tuples(st.just("pay"), payment_cents)),
    max_size=25,
)


@given(events, st.booleans())
def test_remaining_follows_the_cumulative_rule(history, settle_on_payment):
    """Whether payments settle at once or only in the next billing run, every bill ends with
    the remaining amount the totals say - the allocation never depends on timing."""
    book = Book()
    for kind, amount in history:
        if kind == "bill":
            book.add_bill(amount)
            book.settle()
        else:
            book.add_payment(amount)
            if settle_on_payment:
                book.settle()
        book.check_bounds()
    book.settle()
    assert {i: b.remaining for i, b in book.bills.items() if b.amount_due > 0} == book.oracle()
    assert book.settle().is_empty  # idempotent


@given(events)
def test_after_settling_there_is_no_open_bill_or_no_unused_credit(history):
    book = Book()
    for kind, amount in history:
        book.add_bill(amount) if kind == "bill" else book.add_payment(amount)
        book.settle()
        open_bills = [b for b in book.bills.values() if b.is_open]
        unused = [p for p in book.payments.values() if p.left > 0] + [
            b for b in book.bills.values() if b.credit_left > 0
        ]
        assert not (open_bills and unused)
        # oldest first: no open bill is older than a bill that received anything
        if open_bills:
            oldest_open = min(b.bill_date for b in open_bills)
            assert all(
                b.allocated == 0
                for b in book.bills.values()
                if b.bill_date > oldest_open and b.amount_due > 0
            )


@given(events, st.lists(st.integers(min_value=0, max_value=30), max_size=8))
def test_holds_are_skipped_and_releasing_them_settles_by_the_totals(history, holds):
    """A held bill never receives an automatic allocation and never changes on its own;
    states never move back except by the operator; once every hold is released, the
    totals are those of the cumulative rule (which bills are paid may differ)."""
    book = Book()
    held: set[UUID] = set()
    for step, (kind, amount) in enumerate(history):
        if kind == "bill":
            book.add_bill(amount)
        else:
            book.add_payment(amount)
        for index in holds:
            positive = [b for b in book.bills.values() if b.state in ("sent", "partiallyPaid")]
            if index == step and positive:
                target = positive[index % len(positive)]
                book.set_state(target.id, "onHold")
                held.add(target.id)
        before = {i: book.bills[i].allocated for i in held}
        result = book.settle()
        assert not {a.bill_id for a in result.allocations} & held
        assert {i: book.bills[i].allocated for i in held} == before
        book.check_bounds()
    for i in held:
        b = book.bills[i]
        book.set_state(i, paid_state(b.amount_due, b.allocated))
    book.settle()
    book.check_bounds()
    rank = {"sent": 0, "partiallyPaid": 1, "settled": 2}
    for states in book.history.values():
        free = [s for s in states if s != "onHold"]
        assert free == sorted(free, key=lambda s: rank.get(s, 2)), states
    positive = [b for b in book.bills.values() if b.amount_due > 0]
    assert sum((b.remaining for b in positive), ZERO) == sum(book.oracle().values(), ZERO)


@given(
    st.lists(st.integers(min_value=1, max_value=20_000), min_size=1, max_size=6),
    st.integers(min_value=1, max_value=60_000),
    st.data(),
)
def test_explicit_items_are_applied_exactly(bill_cents, payment, data):
    book = Book()
    for c in bill_cents:
        book.add_bill(c)
    p = book.add_payment(payment)
    chosen = data.draw(st.lists(st.sampled_from(list(book.bills)), unique=True))
    items = []
    budget = p.amount
    for bill_id in chosen:
        top = min(book.bills[bill_id].remaining, budget)
        if top <= 0:
            break
        amount = D(data.draw(st.integers(min_value=1, max_value=int(top * 100)))) / 100
        items.append(ExplicitItem(bill_id, amount))
        budget -= amount
    result = book.settle(items, p.id)
    explicit = [(a.bill_id, a.amount) for a in result.allocations if a.kind == "explicit"]
    assert explicit == [(i.bill_id, i.amount) for i in items]
    assert all(a.payment_id == p.id for a in result.allocations)
    book.check_bounds()
    # whatever was not lettered went oldest first, and nothing is left on both sides
    assert not (any(b.is_open for b in book.bills.values()) and book.payments[p.id].left > 0)
