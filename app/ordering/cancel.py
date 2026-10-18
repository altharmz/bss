"""TMF622 cancelProductOrder: undo a completed order before any of it takes effect, or
throw a draft away (it was never processed: nothing to undo).

Orders are processed synchronously (app/ordering/process.py), so there is never an
order in progress to stop. A cancellation instead undoes a completed order whose every
effect is still in the future, in one transaction:

- add item: its product has not started (`created`/`pendingActive`, start after today)
  and nothing else ended it; the product is `cancelled` with zero billable days.
- delete item: the termination it set has not happened (termination after today) and
  is still the product's; the product is open-ended again (`active`, or `created` when
  it was a cancellation of a product that has not started).
- billing has written no charge or refund of the product for the cycle period that
  contains the item's date (its start, or its termination) or any later period: the
  core never charges more than it charged (`InconsistentState`), so a termination
  already reflected in a charge cannot be undone.

Anything else refuses the whole cancellation (CancelRefused -> 409) and nothing is
stored. Locks, in the order billing and orders take them: the order, then the account
FOR UPDATE (as `bill_account`: no run charges the account in between), then the products
in id order (as orders and the status flips do).
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from app.billing.core import cycle_period
from app.events import emit
from app.ordering.states import item_transition, order_transition
from app.repository import orders, parties, products
from app.repository._sql import Row

NOT_STARTED = frozenset({"created", "pendingActive"})


class CancelRefused(ValueError):
    """The order cannot be cancelled (any more); the message says why."""


@dataclass(frozen=True)
class CancelRequest:
    order_id: UUID
    requested: datetime | None = None
    reason: str | None = None
    id: UUID | None = None  # of the cancellation; None: a new one (a client may choose it)


def _item_problem(
    conn: psycopg.Connection, item: Row, product: Row, today: date, cycle_day: int
) -> str | None:
    if item["action"] == "noChange":
        return None  # it changed nothing, so there is nothing to undo
    if item["action"] == "add":
        if product["termination_date"] is not None:
            return "another order already ended its product"
        if product["status"] not in NOT_STARTED or product["start_date"] <= today:
            return f"its product started on {product['start_date']}"
        day = product["start_date"]
    else:
        ends = product["termination_date"]
        if product["terminated_by_item_id"] != item["id"] or ends is None:
            return "its product is no longer ended by this order"
        if ends <= today:
            return f"the termination took effect on {ends}"
        day = ends
    period = cycle_period(day, cycle_day)
    if products.charged_from(conn, product["id"], period.start):
        return f"billing already charged its product for the period from {period.start}"
    return None


def cancel_order(
    conn: psycopg.Connection, request: CancelRequest, *, now: datetime, today: date
) -> Row:
    """Cancel the order; returns the stored cancelProductOrder row. LookupError if the
    order does not exist, CancelRefused if it cannot be cancelled."""
    order = orders.lock_order(conn, request.order_id)
    if order is None:
        raise LookupError(f"productOrder {request.order_id} does not exist")
    if order["state"] == "draft":
        return _cancel_draft(conn, order, request, now)
    if order["state"] != "completed":
        raise CancelRefused(
            f"productOrder {order['id']} is {order['state']}: only a completed order or a"
            " draft can be cancelled"
        )
    account = parties.get_billing_account(conn, order["billing_account_id"], "update")
    assert account is not None  # foreign key, and the order is locked
    items = orders.items_of(conn, order["id"])
    locked = products.lock_products(conn, [i["product_id"] for i in items if i["product_id"]])
    problems = []
    for item in items:
        product = locked[item["product_id"]]
        problem = _item_problem(conn, item, product, today, account["bill_cycle_day"])
        if problem is not None:
            problems.append(f"item {item['item_id']} ({item['action']}): {problem}")
    if problems:
        raise CancelRefused(
            f"productOrder {order['id']} cannot be cancelled: " + "; ".join(problems)
        )
    for item in items:
        product = locked[item["product_id"]]
        if item["action"] != "noChange":
            if item["action"] == "add":
                products.cancel_unstarted(conn, product["id"])
            else:
                products.restore(
                    conn, product["id"], "created" if product["start_date"] > today else "active"
                )
            emit.product(conn, product["id"], "StateChange", at=now, today=today)
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "cancelled"))
    return _cancelled(conn, order, request, now)


def _cancel_draft(
    conn: psycopg.Connection, order: Row, request: CancelRequest, now: datetime
) -> Row:
    """A draft was never processed: nothing to undo. Its items get their first state."""
    for item in orders.items_of(conn, order["id"]):
        orders.set_order_item_state(conn, item["id"], "cancelled")
    return _cancelled(conn, order, request, now)


def _cancelled(conn: psycopg.Connection, order: Row, request: CancelRequest, now: datetime) -> Row:
    order_transition(order["state"], "cancelled")
    orders.set_cancelled(conn, order["id"], at=now, reason=request.reason)
    return orders.insert_cancellation(
        conn,
        order_id=order["id"],
        at=now,
        requested=request.requested,
        reason=request.reason,
        id=request.id,
    )
