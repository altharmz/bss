"""State machine of product orders and their items (TMF622), as a pure function.

Only the transitions this service performs are allowed - a subset of the TMF622 state
model. Orders are processed synchronously:

    draft -> acknowledged                        (PATCH state; then processed as below)
    acknowledged -> inProgress -> completed      (every item valid)
    acknowledged -> rejected                     (at least one reason to refuse)
    completed -> cancelled                       (cancelProductOrder, before it takes effect)
    draft -> cancelled                           (cancelProductOrder: nothing to undo)

Items follow their order; when an order is rejected, the items that were fine are
`cancelled` (never executed), the others `rejected`. A cancelled order's items are all
`cancelled` (app/ordering/cancel.py). A draft's items have no state until it is submitted.

The other states of TMF622 (pending, held, failed, partial, assessingCancellation,
pendingCancellation, inProgress.accepted) belong to asynchronous fulfilment: processing
here is synchronous and all-or-nothing, so they are never reached.
"""

ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"acknowledged", "cancelled"}),
    "acknowledged": frozenset({"inProgress", "rejected"}),
    "inProgress": frozenset({"completed"}),
    "completed": frozenset({"cancelled"}),
}

ITEM_TRANSITIONS: dict[str, frozenset[str]] = {
    "acknowledged": frozenset({"inProgress", "rejected", "cancelled"}),
    "inProgress": frozenset({"completed"}),
    "completed": frozenset({"cancelled"}),
}

# completed is not terminal: a cancelProductOrder may still cancel it
TERMINAL = frozenset({"rejected", "cancelled"})


class InvalidTransition(ValueError):
    pass


def _check(table: dict[str, frozenset[str]], kind: str, current: str, target: str) -> str:
    if target not in table.get(current, frozenset()):
        raise InvalidTransition(f"{kind} cannot go from {current!r} to {target!r}")
    return target


def order_transition(current: str, target: str) -> str:
    """`target` if an order in `current` may move there, else InvalidTransition."""
    return _check(ORDER_TRANSITIONS, "order", current, target)


def item_transition(current: str, target: str) -> str:
    """`target` if an order item in `current` may move there, else InvalidTransition."""
    return _check(ITEM_TRANSITIONS, "order item", current, target)
