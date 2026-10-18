"""Processing a product order (TMF622): validate, then execute or reject, synchronously
and on one connection - the caller's transaction covers all of it.

Two kinds of "no":
- OrderRefused: the order cannot even be stored (it references a billing account,
  offering or product that does not exist, or repeats an item id). The API answers 422.
- A rejection: the order is well-formed but breaks a business rule. It is stored with
  state `rejected` and its reasons (productOrderErrorMessage), and the API answers 201.

All-or-nothing: if any item is refused, nothing is executed. Failing items become
`rejected`, the others `cancelled`. Nested items (TMF622 productOrderItem.productOrderItem)
are items like any other, listed after their parent.

A draft (requestedInitialState `draft`) is stored without being processed: only what
the database needs is checked (the ids, what the order references exists). It is
processed by `submit_draft`, exactly as an order placed at that moment.

A modify item changes its product's offering from requestedStartDate on, billed prorated
(docs/decisions/billing-core.md#offering-changes-2026-10-18).

The billing account (optional in TMF622) is the order's, else the first item's that names
one, else the account of the first product a modify/delete/noChange item names; an
order with none of them is refused. Items that name another account are rejected
(`billingAccountMismatch`, `productOfOtherAccount`), as before.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.config import settings
from app.events import emit
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
    # An add item's new product, as a POST /product chose it: its id and TMF meta
    # attributes (product.id, base_type, schema_location; None = not chosen).
    new_product: products.NewProduct | None = None
    parent_item_id: str | None = None  # nested in that item (listed before this one)
    details: dict[str, Any] | None = None  # documents, stored as sent

    @property
    def related_ids(self) -> list[Any]:
        """The item ids its productOrderItemRelationship[] names."""
        return [r.get("id") for r in (self.details or {}).get("productOrderItemRelationship", [])]


@dataclass(frozen=True)
class OrderRequest:
    billing_account_id: UUID | None  # None: derived from the items
    items: tuple[ItemRequest, ...]
    # None = today (BILLING_TZ). The start date of `add` items and the termination date
    # of `delete` items (our interpretation: TMF has no per-item date).
    requested_start: date | None = None
    order_id: UUID | None = None  # None: a new one (a client may choose it)
    draft: bool = False  # requestedInitialState draft: stored, not processed
    details: dict[str, Any] | None = None  # documents, stored as sent


# Actions whose item names an existing product.
ON_PRODUCT = frozenset({"modify", "delete", "noChange"})


@dataclass(frozen=True)
class Problem:
    item_id: str | None  # None: the order as a whole
    code: str
    reason: str


class OrderRefused(ValueError):
    pass


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(settings.billing_tz))


def _check_structure(request: OrderRequest) -> None:
    if not request.items:
        raise OrderRefused("an order needs at least one item")
    ids = [item.item_id for item in request.items]
    if len(set(ids)) != len(ids):
        raise OrderRefused("productOrderItem ids must be unique within the order")
    for item in request.items:
        for related in item.related_ids:
            if related == item.item_id or related not in ids:
                raise OrderRefused(
                    f"item {item.item_id}: productOrderItemRelationship names {related!r},"
                    " which is not another item of this order"
                )
    # a product can only be ended (or changed) by one item: two would collide on the
    # one-open-delete index, which would abort the transaction and lose a rejected order
    named = [item.product_id for item in request.items if item.product_id is not None]
    if len(set(named)) != len(named):
        raise OrderRefused("a product can only be named by one item of the order")


def _derived_account(conn: psycopg.Connection, request: OrderRequest) -> UUID | None:
    """The order's account: sent, else the first an item names, else the account of the
    first product an item names (a product's account never changes). None: none of them.
    Existence is checked by the caller."""
    if request.billing_account_id is not None:
        return request.billing_account_id
    for item in request.items:
        if item.billing_account_id is not None:
            return item.billing_account_id
    for item in request.items:
        if item.product_id is not None:
            product = products.get_product(conn, item.product_id)
            if product is not None:
                return product["billing_account_id"]
    return None


def place_order(
    conn: psycopg.Connection, request: OrderRequest, *, now: datetime, today: date
) -> UUID:
    """Store the order and process it (a draft: only store it). Returns its id."""
    _check_structure(request)
    if not request.draft:
        return _process(conn, request, _derived_account(conn, request), now=now, today=today)
    account_id = _draft_account(conn, request)
    order = orders.create_order(
        conn,
        id=request.order_id,
        billing_account_id=account_id,
        order_date=now,
        requested_start_date=request.requested_start,
        state="draft",
        requested_initial_state="draft",
        details=request.details,
        items=[_new_item(item, None) for item in request.items],
    )
    return order["id"]


def replace_draft(
    conn: psycopg.Connection, order_id: UUID, request: OrderRequest, *, items: bool
) -> None:
    """A draft's account, start date and (`items`: they changed) items as PATCH left
    them, merged with what it holds by the caller; checked as a draft is on POST."""
    _check_structure(request)
    orders.replace_draft(
        conn,
        order_id,
        billing_account_id=_draft_account(conn, request),
        requested_start_date=request.requested_start,
        items=[_new_item(item, None) for item in request.items] if items else None,
    )


def _draft_account(conn: psycopg.Connection, request: OrderRequest) -> UUID | None:
    """A draft's account (None: none yet), after checking that what it references
    exists. An item's own account is not stored (it must be the order's), so a draft
    item that names another one is refused rather than forgotten."""
    account_id = _derived_account(conn, request)
    if account_id is not None and parties.get_billing_account(conn, account_id) is None:
        raise OrderRefused(f"billingAccount {account_id} does not exist")
    for item in request.items:
        if item.billing_account_id not in (None, account_id):
            raise OrderRefused(
                f"item {item.item_id}: its billingAccount differs from the order's (an order"
                " has one account)"
            )
    _check_references(conn, request)
    return account_id


def submit_draft(
    conn: psycopg.Connection, order_id: UUID, request: OrderRequest, *, now: datetime, today: date
) -> None:
    """Process a draft (locked by the caller) as an order placed now: it keeps its id,
    creation date and documents; `request` is what it holds."""
    _check_structure(request)
    _process(
        conn,
        request,
        _derived_account(conn, request),
        now=now,
        today=today,
        draft_id=order_id,
    )


def _check_references(conn: psycopg.Connection, request: OrderRequest) -> None:
    """What a draft's items reference exists (the foreign keys), without locks."""
    for item in request.items:
        offering_id = item.product_offering_id
        if offering_id and catalog.get_product_offering(conn, offering_id) is None:
            raise OrderRefused(f"item {item.item_id}: productOffering {offering_id} does not exist")
        if item.product_id and products.get_product(conn, item.product_id) is None:
            raise OrderRefused(f"item {item.item_id}: product {item.product_id} does not exist")


def _new_item(
    item: ItemRequest, state: str | None, quote: Row | None = None
) -> orders.NewOrderItem:
    return orders.NewOrderItem(
        item_id=item.item_id,
        action=item.action,
        product_offering_id=item.product_offering_id,
        product_id=item.product_id,
        state=state,
        quoted_price_id=quote["id"] if quote else None,
        quoted_price_name=quote["name"] if quote else None,
        quoted_amount=quote["amount"] if quote else None,
        quoted_currency=quote["currency"] if quote else None,
        parent_item_id=item.parent_item_id,
        details=item.details,
    )


def _process(
    conn: psycopg.Connection,
    request: OrderRequest,
    account_id: UUID | None,
    *,
    now: datetime,
    today: date,
    draft_id: UUID | None = None,
) -> UUID:
    if account_id is None:
        raise OrderRefused(
            "an order needs a billingAccount: on the order, on an item, or through the"
            " product of a modify, delete or noChange item"
        )
    # FOR KEY SHARE until commit: a concurrent delete of the account waits for this order
    account = parties.get_billing_account(conn, account_id, "key share")
    if account is None:
        raise OrderRefused(f"billingAccount {account_id} does not exist")
    for item in request.items:
        offering_id = item.product_offering_id
        # FOR SHARE until commit: a concurrent price or offering delete waits for this
        # order (then sees it), or goes first (then this order sees the catalog without it).
        if offering_id and catalog.lock_offering(conn, offering_id, share=True) is None:
            raise OrderRefused(f"item {item.item_id}: productOffering {offering_id} does not exist")

    # Lock the products of the items before reading anything about them: a concurrent
    # order for the same product waits here and then sees this order's outcome, instead
    # of both validating against the same state (or colliding on the one-open-delete
    # index, which would abort the transaction and lose the rejected order).
    named = [item.product_id for item in request.items if item.product_id is not None]
    locked = products.lock_products(conn, named)
    for item in request.items:
        if item.action in ON_PRODUCT and item.product_id not in locked:
            raise OrderRefused(f"item {item.item_id}: product {item.product_id} does not exist")

    start = request.requested_start or today
    # Validate before anything is inserted: a rejected delete item must never be stored in
    # an open state, or it would collide with the open delete it is rejected for.
    quotes: dict[str, Row] = {}
    problems = _validate(conn, request, account, start, today, locked, quotes)
    failing = {problem.item_id for problem in problems}

    def initial(item: ItemRequest) -> str:
        if not problems:
            return "acknowledged"
        # rejected orders are decided before insert; the transition is still checked
        return item_transition(
            "acknowledged", "rejected" if item.item_id in failing else "cancelled"
        )

    items = [
        _new_item(item, initial(item), None if problems else quotes.get(item.item_id))
        for item in request.items
    ]
    if draft_id is None:
        order = orders.create_order(
            conn,
            id=request.order_id,
            billing_account_id=account["id"],
            order_date=now,
            requested_start_date=start,
            details=request.details,
            items=items,
        )
    else:
        order_transition("draft", "acknowledged")
        order = orders.submit_draft(
            conn,
            draft_id,
            billing_account_id=account["id"],
            requested_start_date=start,
            items=items,
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
    quotes: dict[str, Row],
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
            for code, reason in _item_problems(conn, item, account, start, today, locked, quotes)
        ]
    return problems


def _item_problems(
    conn: psycopg.Connection,
    item: ItemRequest,
    account: Row,
    start: date,
    today: date,
    locked: dict[UUID, Row],
    quotes: dict[str, Row],
) -> list[tuple[str, str]]:
    if item.billing_account_id not in (None, account["id"]):
        return [("billingAccountMismatch", "the item's billingAccount differs from the order's")]
    if item.action == "modify":
        return _modify_problems(conn, item, account, start, today, locked, quotes)
    if item.action == "add":
        return _add_problems(conn, item, account, start, quotes)
    if item.action == "noChange":
        return _unchanged_problems(item, account, locked)
    return _delete_problems(conn, item, account, start, today, locked)


def _unchanged_problems(
    item: ItemRequest, account: Row, locked: dict[UUID, Row]
) -> list[tuple[str, str]]:
    """A noChange item names a product of the order's account that has not ended (the
    context of the other items, e.g. a bundle's member that stays)."""
    product = locked[item.product_id]
    if product["billing_account_id"] != account["id"]:
        return [("productOfOtherAccount", "the product belongs to another billing account")]
    if product["status"] in ENDED:
        return [("productAlreadyEnded", f"the product is already {product['status']}")]
    return []


def _modify_problems(
    conn: psycopg.Connection,
    item: ItemRequest,
    account: Row,
    start: date,
    today: date,
    locked: dict[UUID, Row],
    quotes: dict[str, Row],
) -> list[tuple[str, str]]:
    """A modify item changes the product's offering from `start` (the change day D) on
    (docs/decisions/tmf-api.md#modify-items-2026-10-18). At most one change per product
    waits to take effect, and it must come before any known termination: together with
    the delete rule (openModifyItem) a charge is never cut short twice."""
    product = locked[item.product_id]
    if product["billing_account_id"] != account["id"]:
        return [("productOfOtherAccount", "the product belongs to another billing account")]
    ends = product["termination_date"]
    if product["status"] in ENDED or (ends is not None and ends <= start):
        return [
            (
                "productAlreadyEnded",
                f"the product is {product['status']}"
                + (f" and ends on {ends}, on or before {start}" if ends else ""),
            )
        ]
    if start < product["start_date"]:
        return [
            (
                "productNotStarted",
                f"the product starts on {product['start_date']}; its offering can change"
                " from that day on",
            )
        ]
    pending = products.pending_change(conn, product["id"], today)
    last = products.last_change(conn, product["id"])
    if pending is not None or (last is not None and last["change_date"] >= start):
        day = (pending or last)["change_date"]
        return [("openModifyItem", f"another order already changes its offering on {day}")]
    if products.offering_on(conn, product["id"], start) == item.product_offering_id:
        return [("offeringUnchanged", f"the product is on this offering on {start} already")]
    return _add_problems(conn, item, account, start, quotes)


def _add_problems(
    conn: psycopg.Connection,
    item: ItemRequest,
    account: Row,
    start: date,
    quotes: dict[str, Row],
) -> list[tuple[str, str]]:
    offering = catalog.get_product_offering(conn, item.product_offering_id)
    assert offering is not None  # checked before the order was stored
    at = _midnight(start)
    if not offering["is_sellable"] or offering["lifecycle_status"] != "Active":
        return [("offeringNotSellable", f"productOffering {offering['name']} is not for sale")]
    problems = []
    if offering["valid_for"] is not None and at not in offering["valid_for"]:  # None: always
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
    else:
        quotes[item.item_id] = price
    if price is not None and (gap := _price_gap(conn, price)) is not None:
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
    pending = products.pending_change(conn, product["id"], today)
    if pending is not None and start <= pending["change_date"]:
        return [
            (
                "openModifyItem",
                f"an order changes its offering on {pending['change_date']}: cancel that order"
                " to end the product on or before that day",
            )
        ]
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
                new=item.new_product,
            )
            orders.set_order_item_product(conn, row["id"], product["id"])
            emit.product(conn, product["id"], "Create", at=now, today=today)
        elif item.action == "noChange":
            pass  # nothing to do: the product stays as it is
        elif item.action == "modify":
            products.change_offering(
                conn,
                item.product_id,
                change_date=start,
                product_offering_id=item.product_offering_id,
                changed_by_item_id=row["id"],
                # in effect today: announced now; else by the worker's run of the day
                announced_at=now if start <= today else None,
            )
            if start <= today:
                emit.product(conn, item.product_id, "AttributeValueChange", at=now, today=today)
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
            emit.product(conn, product["id"], "StateChange", at=now, today=today)
        orders.set_order_item_state(conn, row["id"], item_transition(progress, "completed"))
    orders.set_order_state(
        conn, order["id"], order_transition(state, "completed"), completion_date=now
    )
