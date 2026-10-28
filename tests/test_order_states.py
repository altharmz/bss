"""The order / order item state machine (app/ordering/states.py) as a pure function."""

import pytest

from app.ordering.states import (
    ITEM_TRANSITIONS,
    ORDER_TRANSITIONS,
    TERMINAL,
    InvalidTransition,
    item_transition,
    order_transition,
)
from tests.test_enum_lock import _spec_enum

pytestmark = pytest.mark.smoke

ORDER_ALLOWED = [
    ("draft", "acknowledged"),  # PATCH state: submitted
    ("draft", "cancelled"),  # cancelProductOrder: thrown away
    ("acknowledged", "inProgress"),
    ("acknowledged", "rejected"),
    ("inProgress", "completed"),
    ("inProgress", "cancelled"),  # prepaid purchase before its first grant
    ("inProgress", "failed"),  # prepaid dated status change revalidated on its date
    ("completed", "cancelled"),  # cancelProductOrder
]
ITEM_ALLOWED = [
    ("acknowledged", "inProgress"),
    ("acknowledged", "rejected"),
    ("acknowledged", "cancelled"),
    ("inProgress", "completed"),
    ("inProgress", "cancelled"),  # prepaid purchase before its first grant
    ("inProgress", "failed"),  # prepaid dated status change revalidated on its date
    ("completed", "cancelled"),  # its order is cancelled
]


def test_the_allowed_transitions_are_exactly_these():
    assert sorted((a, b) for a, targets in ORDER_TRANSITIONS.items() for b in targets) == sorted(
        ORDER_ALLOWED
    )
    assert sorted((a, b) for a, targets in ITEM_TRANSITIONS.items() for b in targets) == sorted(
        ITEM_ALLOWED
    )


@pytest.mark.parametrize(("current", "target"), ORDER_ALLOWED)
def test_allowed_order_transitions(current, target):
    assert order_transition(current, target) == target


@pytest.mark.parametrize(("current", "target"), ITEM_ALLOWED)
def test_allowed_item_transitions(current, target):
    assert item_transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("acknowledged", "completed"),  # skipping inProgress
        ("inProgress", "rejected"),  # validation is over once processing started
        ("inProgress", "acknowledged"),
        ("completed", "inProgress"),
        ("rejected", "acknowledged"),
        ("rejected", "completed"),
        ("acknowledged", "acknowledged"),
        ("acknowledged", "cancelled"),  # only a completed order is cancelled
        ("rejected", "cancelled"),
        ("cancelled", "completed"),
        ("acknowledged", "partial"),
        ("acknowledged", "nonsense"),
    ],
)
def test_forbidden_order_transitions(current, target):
    with pytest.raises(InvalidTransition, match=f"order cannot go from '{current}'"):
        order_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("acknowledged", "completed"),
        ("inProgress", "rejected"),
        ("completed", "rejected"),
        ("cancelled", "inProgress"),
        ("rejected", "inProgress"),
        ("acknowledged", "held"),
    ],
)
def test_forbidden_item_transitions(current, target):
    with pytest.raises(InvalidTransition, match="order item cannot go"):
        item_transition(current, target)


def test_terminal_states_have_no_way_out():
    for state in TERMINAL:
        assert state not in ORDER_TRANSITIONS and state not in ITEM_TRANSITIONS


def test_every_state_used_is_a_tmf622_state():
    order_states = set(
        _spec_enum("TMF622-ProductOrdering-v5.0.0.oas.yaml", "ProductOrderStateType")
    )
    item_states = set(
        _spec_enum("TMF622-ProductOrdering-v5.0.0.oas.yaml", "ProductOrderItemStateType")
    )
    used_order = {s for pair in ORDER_ALLOWED for s in pair}
    used_item = {s for pair in ITEM_ALLOWED for s in pair}
    assert used_order <= order_states
    assert used_item <= item_states
