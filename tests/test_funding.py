from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.billing.funding import InsufficientFunds, Source, SourceAmount, reconcile, select_sources
from app.billing.model import InconsistentState

pytestmark = pytest.mark.smoke


def source(**kwargs):
    return replace(
        Source("payment", UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), D("50")), **kwargs
    )


def test_combined_consumption_and_holds_bound_selection():
    funds = source(consumed=D("20"), held=D("10"))
    assert select_sources([funds], D("20")) == (SourceAmount("payment", funds.id, D("20")),)
    with pytest.raises(InsufficientFunds) as error:
        select_sources([funds], D("20.01"))
    assert (error.value.required, error.value.available) == (D("20.01"), D("20"))
    assert funds.held == D("10")


def test_selection_is_deterministic_and_preserves_legacy_source_priority():
    payment = source()
    credit = source(kind="creditNote", available_at=datetime(2025, 1, 1, tzinfo=UTC))
    newer = source(id=UUID(int=2))
    expected = (
        SourceAmount("payment", payment.id, D("50")),
        SourceAmount("payment", newer.id, D("50")),
        SourceAmount("creditNote", credit.id, D("0.01")),
    )
    assert select_sources([credit, newer, payment], D("100.01")) == expected
    assert select_sources([payment, credit, newer], D("100.01")) == expected


@pytest.mark.parametrize("amount", [D("-1"), D("NaN"), D("Infinity"), 1.0])
def test_invalid_request_amount(amount):
    with pytest.raises(ValueError):
        select_sources([source()], amount)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": D("-1")},
        {"consumed": D("NaN")},
        {"held": D("Infinity")},
        {"consumed": D("40"), "held": D("11")},
        {"kind": "unknown"},
    ],
)
def test_inconsistent_sources_are_technical_failures_even_for_zero_request(changes):
    with pytest.raises(InconsistentState):
        select_sources([source(**changes)], D("0"))


def test_duplicate_source_refused_but_same_uuid_in_different_kinds_allowed():
    with pytest.raises(InconsistentState):
        select_sources([source(), source()], D("1"))
    assert len(select_sources([source(), source(kind="adjustment")], D("51"))) == 2


def test_reconciliation_does_not_subtract_allocations_twice():
    before = source(held=D("10"))
    after = source(consumed=D("20"), held=D("10"))
    assert reconcile([before], D("20"), D("30")) == D("10")
    assert reconcile([after], D("20"), D("10")) == D("10")
    with pytest.raises(InconsistentState):
        reconcile([after], D("19.99"), D("10"))


def test_zero_and_negative_ledger_balances():
    assert select_sources([], D("0")) == ()
    assert reconcile([], D("-5"), D("5")) == D("-5")
