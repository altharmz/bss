"""Qualifying product offerings (TMF679): a dry run of the order engine.
docs/decisions/qualification.md.

`qualify` places the order the qualification items describe inside a savepoint, reads back
the reasons the engine rejected it for, and rolls the savepoint back: nothing the engine wrote
stays (the order, its products, number claims, prepaid reservations, network commands,
events). A qualification therefore answers with exactly the reasons an order placed at that
moment would be rejected for, under the same codes (`productOrderErrorMessage.code` becomes
`eligibilityResultReason.code`). There is no second list of rules to keep in step: a new
order rule is a new qualification reason the day it lands.

The request carries no actor, so the agent's PIN screening is skipped: a PIN authenticates an
order, it says nothing about eligibility, and a dry run must never count PIN attempts
(app/ordering/security.py). Those refusals are `ORDER_ONLY`. A SIM swap lock still applies.

What the dry run cannot see: an order placed later meets the catalog, numbers and balances of
that moment. A qualification is an answer for its moment, not a reservation.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from app.ordering.process import OrderRequest, Problem, place_order
from app.repository import orders

# Order refusals a qualification never gives, and why (tests/ordering/test_qualification.py
# lists every order refusal code against this table).
ORDER_ONLY: dict[str, str] = {
    "pinRequired": "the agent's PIN authenticates an order; it is no eligibility rule",
    "pinInvalid": "the agent's PIN authenticates an order; it is no eligibility rule",
    "pinNotSet": "the agent's PIN authenticates an order; it is no eligibility rule",
    "pinLocked": "the agent's PIN authenticates an order; it is no eligibility rule",
    "portOutLocked": "only a carrier's port-out meets it, never an order a client places",
}

# Reasons that only say an input is missing (a number, a SIM, an address, the plan of an
# add-on pack): a query has no such inputs, so they do not disqualify an offering there.
# A check with the inputs is still needed before the order (docs/decisions/qualification.md).
NEEDS_INPUT = frozenset(
    {
        "numberRequired",
        "imsiRequired",
        "serviceAddressRequired",
        "e911AddressRequired",
        "addOnParentRequired",
    }
)


@dataclass(frozen=True)
class Verdict:
    """What the order engine said: the order's billing account and its refusals (none: it
    would be accepted)."""

    account_id: UUID
    problems: tuple[Problem, ...]

    def of(self, item_id: str) -> list[Problem]:
        """The reasons that disqualify one item: its own and the order's as a whole."""
        return [p for p in self.problems if p.item_id in (None, item_id)]


class _Undo(Exception):
    def __init__(self, verdict: Verdict) -> None:
        super().__init__("qualification dry run")
        self.verdict = verdict


def qualify(
    conn: psycopg.Connection, request: OrderRequest, *, now: datetime, today: date
) -> Verdict:
    """The engine's verdict on `request`, placed now and undone. Raises OrderRefused, as
    placing it would, when it references something that does not exist."""
    assert not request.draft and request.order_id is None and request.actor is None
    try:
        with conn.transaction():  # a savepoint: the caller's transaction goes on
            order_id = place_order(conn, request, now=now, today=today)
            order = orders.get_order(conn, order_id)
            assert order is not None
            item_ids = {row["id"]: row["item_id"] for row in order["items"]}
            problems = tuple(
                Problem(
                    item_ids.get(message["product_order_item_id"]),
                    message["code"],
                    message["reason"],
                )  # fmt: skip
                for message in orders.error_messages(conn, order_id)
            )
            raise _Undo(Verdict(order["billing_account_id"], problems))
    except _Undo as undo:
        return undo.verdict
