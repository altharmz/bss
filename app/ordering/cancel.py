"""TMF622 cancelProductOrder: undo a completed order before any of it takes effect, or
throw a draft away (it was never processed: nothing to undo).

Prepaid inProgress orders may be cancelled before their first grant, under the
command identity lock: release money and number claims and retain a cancelled
command result. A prepaid termination cannot be cancelled once accepted. A prepaid
status order is undone like a postpaid one; its product gets back the manual cause
and effective status the earlier changes left. Postpaid cancellation undoes a
completed order whose every effect is still in the future, in one transaction:

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
from datetime import date, datetime, timedelta
from uuid import UUID

import psycopg

from app.events import emit
from app.ordering import numbers as number_orders
from app.ordering import prepaid as prepaid_orders
from app.ordering.states import item_transition, order_transition
from app.repository import billing, funding, orders, parties, products
from app.repository import prepaid_commands as commands
from app.repository._sql import Row, fetch_one

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
        if item["product_offering_id"] is None:
            claim = fetch_one(
                conn,
                """SELECT * FROM number_assignment WHERE assigned_by_item_id = %s
                AND cancelled_at IS NULL""",
                (item["id"],),
            )
            if claim is None or claim["effective_from"] <= today:
                return "the number change has already taken effect"
            if claim["ended_by_item_id"] is not None:
                return "another order ended the number assignment"
            return None
        return _modify_problem(conn, item, product, today, cycle_day)
    if item["action"] == "add":
        if any(
            c["assigned_by_item_id"] != item["id"]
            for c in number_orders.claims(conn, [product["id"]])
        ):
            return "another order changes its product number"
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
    period = billing.product_period(conn, product, day, cycle_day)
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
    # Use the old schedule just before the change, whose charge may have been cut.
    mid_period = (
        billing.product_period(conn, product, day - timedelta(days=1), cycle_day).end != day
    )
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
    # Posting takes command identity before account/product/order locks.
    commands.lock_identity(conn, request.order_id)
    command = commands.get(conn, request.order_id)
    order = orders.lock_order(conn, request.order_id)
    if order is None:
        raise LookupError(f"productOrder {request.order_id} does not exist")
    if order["state"] == "draft":
        return _cancel_draft(conn, order, request, now)
    if command is not None:
        return _cancel_prepaid(conn, order, command, request, now, today)
    if order["state"] != "completed":
        raise CancelRefused(
            f"productOrder {order['id']} is {order['state']}: only a completed order or a"
            " draft can be cancelled"
        )
    account = parties.get_billing_account(conn, order["billing_account_id"], "update")
    assert account is not None  # foreign key, and the order is locked
    items = orders.items_of(conn, order["id"])
    locked = products.lock_products(conn, [i["product_id"] for i in items if i["product_id"]])
    number_rows = number_orders.lock(conn, list(locked))
    problems = []
    for item in items:
        product = locked[item["product_id"]]
        problem = _item_problem(
            conn,
            item,
            product | {"currency": account["currency"]},
            today,
            account["bill_cycle_day"],
        )
        if problem is not None:
            problems.append(f"item {item['item_id']} ({item['action']}): {problem}")
    number_problem = number_orders.cancellation_problem(conn, items)
    if number_problem:
        problems.append(number_problem)
    if problems:
        raise CancelRefused(
            f"productOrder {order['id']} cannot be cancelled: " + "; ".join(problems)
        )
    old_numbers = number_orders.before(conn, number_rows, today)
    number_orders.cancel(conn, items, now)
    for item in items:
        product = locked[item["product_id"]]
        if item["action"] == "modify" and item["product_status"] is not None:
            change = products.undo_status_change(conn, item["id"])
            if change["applied_at"] is not None and account["rating_type"] == "prepaid":
                if prepaid_orders.restore_status(conn, product["id"], account, today):
                    emit.product(conn, product["id"], "StateChange", at=now, today=today)
            elif change["applied_at"] is not None:
                products.restore_applied_status(conn, product["id"])
                emit.product(conn, product["id"], "StateChange", at=now, today=today)
        elif item["action"] == "modify":
            if item["product_offering_id"] is not None:
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
    number_orders.changed(conn, number_rows, old_numbers, now, today)
    return _cancelled(conn, order, request, now)


def _cancel_prepaid(conn, order, command, request, now, today):
    if "terminations" in command["request"]:
        raise CancelRefused("a prepaid termination cannot be cancelled once accepted")
    if order["state"] != "inProgress" or command["state"] == "completed":
        raise CancelRefused("prepaid order cannot be cancelled after its first grant")
    parties.get_billing_account(conn, order["billing_account_id"], "update")
    items = orders.items_of(conn, order["id"])
    locked = products.lock_products(conn, [i["product_id"] for i in items])
    if fetch_one(
        conn, "SELECT id FROM funded_interval WHERE command_id = %s LIMIT 1", (command["id"],)
    ):
        raise CancelRefused("prepaid order already has a funded grant")
    number_rows = number_orders.lock(conn, list(locked))
    problem = number_orders.cancellation_problem(conn, items)
    if problem:
        raise CancelRefused(problem)
    old_numbers = number_orders.before(conn, number_rows, today)
    number_orders.cancel(conn, items, now)
    hold = fetch_one(
        conn, "SELECT hold_id FROM funding_command_hold WHERE command_id = %s", (command["id"],)
    )
    if hold:
        funding.release(
            conn,
            order["billing_account_id"],
            hold["hold_id"],
            released_at=now,
            command_id=command["id"],
        )
    for item in items:
        products.cancel_unstarted(conn, item["product_id"])
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "cancelled"))
        emit.product(conn, item["product_id"], "StateChange", at=now, today=today)
    number_orders.changed(conn, number_rows, old_numbers, now, today)
    commands.complete(conn, command["id"], {"outcome": "cancelled"}, now=now)
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
