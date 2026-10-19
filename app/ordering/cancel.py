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
- modify item: its offering change has not taken effect (change day after today) and
  billing does not reflect it yet (`_modify_problem`); its change row is deleted. A
  status change (suspension, resumption) can also be undone on its effective day:
  billing does not read it. A later termination must be cancelled first.

A product whose termination is undone gets back the status its status changes left it
in (`active`, or `suspended`).

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
    if item["action"] == "modify" and item["product_status"] is not None:
        return _status_problem(conn, item, product, today)
    if item["action"] == "modify":
        return _modify_problem(conn, item, product, today, cycle_day)
    if item["action"] == "add":
        if product["termination_date"] is not None:
            return "another order already ended its product"
        if products.change_items(conn, product["id"]):
            return "another order changes its product's offering"
        if products.status_changes_of(conn, product["id"]):
            return "another order changes its product's status"
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


def _modify_problem(
    conn: psycopg.Connection, item: Row, product: Row, today: date, cycle_day: int
) -> str | None:
    """A change can be undone before its day, until billing reflects it: an ABR from the
    change day on (the new offering's charge, the old one's refund, later periods), or the
    old offering's charge cut short at the change day inside a period. A full charge of
    the old offering not yet refunded is still right without the change."""
    change = next(
        (
            c
            for c in products.changes_of(conn, product["id"])
            if c["changed_by_item_id"] == item["id"]
        ),
        None,
    )
    if change is None:
        return "its offering change is no longer the product's"
    day = change["change_date"]
    if day <= today:
        return f"the offering change took effect on {day}"
    mid_period = cycle_period(day, cycle_day).start != day
    if products.change_billed(conn, product["id"], day, mid_period=mid_period):
        return f"billing already reflects the offering change on {day}"
    return None


def _status_problem(conn: psycopg.Connection, item: Row, product: Row, today: date) -> str | None:
    change = next(
        (
            c
            for c in products.status_changes_of(conn, product["id"])
            if c["changed_by_item_id"] == item["id"]
        ),
        None,
    )
    if change is None:
        return "its status change is no longer the product's"
    if change["change_date"] < today:
        return f"the status change took effect on {change['change_date']}"
    if product["termination_date"] is not None:
        return "another order ended its product: cancel that order first"
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
        if item["action"] == "modify" and item["product_status"] is not None:
            change = products.undo_status_change(conn, item["id"])
            if change["applied_at"] is not None:
                products.restore_applied_status(conn, product["id"])
                emit.product(conn, product["id"], "StateChange", at=now, today=today)
        elif item["action"] == "modify":
            products.undo_change(conn, item["id"])
            emit.product(conn, product["id"], "AttributeValueChange", at=now, today=today)
        elif item["action"] != "noChange":
            if item["action"] == "add":
                products.cancel_unstarted(conn, product["id"])
            else:
                started = product["start_date"] <= today
                products.restore(
                    conn,
                    product["id"],
                    products.status_after_changes(conn, product["id"]) if started else "created",
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
