"""Processing a product order (TMF622): validate, then execute or reject, synchronously
and on one connection - the caller's transaction covers all of it.

Two kinds of "no":
- OrderRefused: the order cannot even be stored (it references a billing account,
  offering or product that does not exist, or repeats an item id). The API answers 422.
- A rejection: the order is well-formed but breaks a business rule. It is stored with
  state `rejected` and its reasons (productOrderErrorMessage), and the API answers 201.

All-or-nothing: if any item is refused, nothing is executed. Failing items become
`rejected`, the others `cancelled`.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.config import settings
from app.ordering.states import item_transition, order_transition
from app.repository import catalog, orders, parties, products
from app.repository._sql import Row

# Product statuses after which a product can no longer be deleted.
ENDED = frozenset({"terminated", "cancelled", "aborted"})


@dataclass(frozen=True)
class ItemRequest:
    item_id: str
    action: str
    product_offering_id: UUID | None = None
    product_id: UUID | None = None
    # TMF allows an account per item; ours must equal the order's.
    billing_account_id: UUID | None = None


@dataclass(frozen=True)
class OrderRequest:
    billing_account_id: UUID
    items: tuple[ItemRequest, ...]
    # None = today (BILLING_TZ). The start date of `add` items and the termination date
    # of `delete` items (our interpretation: TMF has no per-item date).
    requested_start: date | None = None


@dataclass(frozen=True)
class Problem:
    item_id: str | None  # None: the order as a whole
    code: str
    reason: str


class OrderRefused(ValueError):
    pass


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(settings.billing_tz))


def place_order(
    conn: psycopg.Connection, request: OrderRequest, *, now: datetime, today: date
) -> UUID:
    """Store the order and process it. Returns its id."""
    if not request.items:
        raise OrderRefused("an order needs at least one item")
    ids = [item.item_id for item in request.items]
    if len(set(ids)) != len(ids):
        raise OrderRefused("productOrderItem ids must be unique within the order")
    account = parties.get_billing_account(conn, request.billing_account_id)
    if account is None:
        raise OrderRefused(f"billingAccount {request.billing_account_id} does not exist")
    for item in request.items:
        offering_id = item.product_offering_id
        if offering_id and catalog.get_product_offering(conn, offering_id) is None:
            raise OrderRefused(f"item {item.item_id}: productOffering {offering_id} does not exist")

    # Lock the products of delete items before reading anything about them: a concurrent
    # order for the same product waits here and then sees this order's outcome, instead
    # of both validating against the same state (or colliding on the one-open-delete
    # index, which would abort the transaction and lose the rejected order).
    deleted = [item.product_id for item in request.items if item.action == "delete"]
    if len(set(deleted)) != len(deleted):
        raise OrderRefused("a product can only be deleted by one item of the order")
    locked = products.lock_products(conn, [pid for pid in deleted if pid is not None])
    for item in request.items:
        if item.action == "delete" and item.product_id not in locked:
            raise OrderRefused(f"item {item.item_id}: product {item.product_id} does not exist")

    start = request.requested_start or today
    # Validate before anything is inserted: a rejected delete item must never be stored in
    # an open state, or it would collide with the open delete it is rejected for.
    problems = _validate(conn, request, account, start, today, locked)
    failing = {problem.item_id for problem in problems}

    def initial(item: ItemRequest) -> str:
        if not problems:
            return "acknowledged"
        # rejected orders are decided before insert; the transition is still checked
        return item_transition(
            "acknowledged", "rejected" if item.item_id in failing else "cancelled"
        )

    order = orders.create_order(
        conn,
        billing_account_id=account["id"],
        order_date=now,
        requested_start_date=start,
        items=[
            orders.NewOrderItem(
                item_id=item.item_id,
                action=item.action,
                product_offering_id=item.product_offering_id,
                product_id=item.product_id,
                state=initial(item),
            )
            for item in request.items
        ],
    )
    rows = {row["item_id"]: row for row in order["items"]}
    if problems:
        _reject(conn, order, rows, problems, now)
    else:
        _execute(conn, order, request, rows, start, today, locked, now)
    return order["id"]


def _validate(
    conn: psycopg.Connection,
    request: OrderRequest,
    account: Row,
    start: date,
    today: date,
    locked: dict[UUID, Row],
) -> list[Problem]:
    problems: list[Problem] = []
    if account["state"] != "Active":
        problems.append(Problem(None, "accountNotActive", f"billing account is {account['state']}"))
    if start < today:
        problems.append(
            Problem(None, "requestedStartDateInPast", f"{start} is before today ({today})")
        )
    for item in request.items:
        problems += [
            Problem(item.item_id, code, reason)
            for code, reason in _item_problems(conn, item, account, start, today, locked)
        ]
    return problems


def _item_problems(
    conn: psycopg.Connection,
    item: ItemRequest,
    account: Row,
    start: date,
    today: date,
    locked: dict[UUID, Row],
) -> list[tuple[str, str]]:
    if item.billing_account_id not in (None, account["id"]):
        return [("billingAccountMismatch", "the item's billingAccount differs from the order's")]
    if item.action in ("modify", "noChange"):
        return [("actionNotSupported", f"action {item.action} is not supported")]
    if item.action == "add":
        return _add_problems(conn, item, account, start)
    return _delete_problems(conn, item, account, start, today, locked)


def _add_problems(
    conn: psycopg.Connection, item: ItemRequest, account: Row, start: date
) -> list[tuple[str, str]]:
    offering = catalog.get_product_offering(conn, item.product_offering_id)
    assert offering is not None  # checked before the order was stored
    at = _midnight(start)
    if not offering["is_sellable"] or offering["lifecycle_status"] != "Active":
        return [("offeringNotSellable", f"productOffering {offering['name']} is not for sale")]
    problems = []
    if at not in offering["valid_for"]:
        problems.append(("offeringNotValid", f"productOffering is not valid on {start}"))
    price = catalog.price_at(
        conn,
        product_offering_id=offering["id"],
        price_type="recurring",
        currency=account["currency"],
        at=at,
    )
    if price is None:
        problems.append(
            ("noRecurringPrice", f"no recurring price in {account['currency']} on {start}")
        )
    elif (gap := _price_gap(conn, price)) is not None:
        local = gap.astimezone(ZoneInfo(settings.billing_tz))
        problems.append(
            (
                "priceEnds",
                f"the recurring price in {account['currency']} ends at"
                f" {local:%Y-%m-%d %H:%M} and no price follows it",
            )
        )
    return problems


def _price_gap(conn: psycopg.Connection, price: Row) -> datetime | None:
    """Where the chain of recurring prices that starts with `price` (the one valid at the
    product's start) runs out, or None if it is gapless and ends open-ended.

    A product is open-ended when it is created, so it needs a price for every future day:
    each price that ends needs a successor of the same offering, type and currency that
    starts exactly at that end. Gaps before the start date do not matter. With the
    catalog rule that ending a price never leaves a product without one (D-W18), this
    keeps the invariant "a live product has a price for every future day". The EXCLUDE
    constraint makes the starts strictly increasing, so the walk ends."""
    current = price
    while current["valid_for"].upper is not None:
        successor = catalog.successor_price(
            conn,
            offering_id=current["product_offering_id"],
            price_type=current["price_type"],
            currency=current["currency"],
            starts_at=current["valid_for"].upper,
            exclude_id=current["id"],
        )
        if successor is None:
            return current["valid_for"].upper
        current = successor
    return None


def _delete_problems(
    conn: psycopg.Connection,
    item: ItemRequest,
    account: Row,
    start: date,
    today: date,
    locked: dict[UUID, Row],
) -> list[tuple[str, str]]:
    product = locked[item.product_id]
    if product["billing_account_id"] != account["id"]:
        return [("productOfOtherAccount", "the product belongs to another billing account")]
    if product["status"] in ENDED or product["termination_date"] is not None:
        return [("productAlreadyEnded", f"the product is already {product['status']}")]
    if orders.open_delete_items(conn, product["id"]):
        return [("openDeleteItem", "another order is already deleting this product")]
    if product["start_date"] > today and start > product["start_date"]:
        # Not started yet: a delete cancels it. Terminating it later would be an order
        # for a start date and an end date at once - not what a delete means.
        return [
            (
                "productNotStarted",
                f"the product starts on {product['start_date']}; before that it can only be"
                " cancelled (requestedStartDate on or before its start date)",
            )
        ]
    return []


def _reject(
    conn: psycopg.Connection,
    order: Row,
    rows: dict[str, Row],
    problems: list[Problem],
    now: datetime,
) -> None:
    """The items were stored in their final states already (see place_order)."""
    for problem in problems:
        orders.add_error_message(
            conn,
            order_id=order["id"],
            order_item_id=rows[problem.item_id]["id"] if problem.item_id else None,
            code=problem.code,
            reason=problem.reason,
            occurred_at=now,
        )
    orders.set_order_state(conn, order["id"], order_transition(order["state"], "rejected"))


def _execute(
    conn: psycopg.Connection,
    order: Row,
    request: OrderRequest,
    rows: dict[str, Row],
    start: date,
    today: date,
    locked: dict[UUID, Row],
    now: datetime,
) -> None:
    state = order_transition(order["state"], "inProgress")
    orders.set_order_state(conn, order["id"], state)
    for item in request.items:
        row = rows[item.item_id]
        progress = item_transition(row["state"], "inProgress")
        orders.set_order_item_state(conn, row["id"], progress)
        if item.action == "add":
            product = products.create_product(
                conn,
                billing_account_id=order["billing_account_id"],
                product_offering_id=item.product_offering_id,
                created_by_item_id=row["id"],
                start_date=start,
                # a future start stays `created` until the stage 4 worker activates it;
                # billing is driven by the dates, not by this status
                status="active" if start <= today else "created",
            )
            orders.set_order_item_product(conn, row["id"], product["id"])
        else:
            product = locked[item.product_id]
            if product["start_date"] > today:
                # never activated: cancelled, with zero billable days
                products.terminate_product(
                    conn,
                    product["id"],
                    termination_date=product["start_date"],
                    terminated_by_item_id=row["id"],
                    status="cancelled",
                )
            else:
                products.terminate_product(
                    conn,
                    product["id"],
                    termination_date=start,
                    terminated_by_item_id=row["id"],
                    # mirrors `created`: a future end is pending until the stage 4 worker
                    # flips it on the date; billing is driven by termination_date
                    status="terminated" if start <= today else "pendingTerminate",
                )
        orders.set_order_item_state(conn, row["id"], item_transition(progress, "completed"))
    orders.set_order_state(
        conn, order["id"], order_transition(state, "completed"), completion_date=now
    )
