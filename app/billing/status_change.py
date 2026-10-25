"""Dated prepaid suspensions and resumptions, applied by the billing owner on their date.

Acceptance (app/ordering/prepaid.py) retains the order inProgress with one `order`
command due on the date. Posting revalidates and then runs P3f's same-day execution:
a suspension sets the manual cause and keeps the paid interval; a resumption lifts it
and, after expiry, queues the renewal of the expired grant. If the product ended or
the account is no longer Active for a resumption by then, the items and the order end
`failed` with a productOrderErrorMessage, and the retained change is dropped.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.billing import entitlement
from app.events import emit
from app.ordering.states import item_transition, order_transition
from app.repository import orders, products
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_one
from app.worker.db import holds_ownership

ENDED = frozenset({"terminated", "cancelled", "aborted"})


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    status: str = Field(pattern="^(active|suspended)$")


class StatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    status_changes: list[Change] = Field(min_length=1)


def renewal_ids(conn: psycopg.Connection, request: dict) -> list[UUID]:
    """The renewal identities a resumption may queue, to lock before the account."""
    ids = [
        UUID(c["product_id"]) for c in request.get("status_changes", []) if c["status"] == "active"
    ]
    out = []
    for product_id in ids:
        grant = entitlement.latest_grant(conn, product_id)
        if grant is not None:
            out.append(commands.renewal_id(grant["id"]))
    return sorted(out)


def post_status_change(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Apply (or fail) a dated status order and complete its command atomically."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        request = StatusChangeRequest.model_validate(cmd["request"])
        account = fetch_one(
            conn, "SELECT * FROM billing_account WHERE id = %s", (cmd["billing_account_id"],)
        )
        if cmd["kind"] != "order" or account["rating_type"] != "prepaid":
            raise ValueError("status change requires a prepaid order command")
        orders.lock_order(conn, request.order_id)
        order = orders.get_order(conn, request.order_id)
        wanted = {c.product_id: c.status for c in request.status_changes}
        locked = products.lock_products(conn, list(wanted))
        items = {i["id"]: i for i in order["items"]} if order else {}
        if (
            request.order_id != cmd["id"]
            or order is None
            or order["billing_account_id"] != account["id"]
            or order["state"] != "inProgress"
            or any(
                i["action"] != "modify" or i["state"] != "inProgress" or i["product_status"] is None
                for i in items.values()
            )
            or {i["product_id"]: i["product_status"] for i in items.values()} != wanted
        ):
            raise ValueError("status command must match its entire pending order")
        problems = {}
        for item in items.values():
            product = locked[item["product_id"]]
            if product["status"] in ENDED or product["termination_date"] is not None:
                problems[item["id"]] = (
                    "productAlreadyEnded",
                    f"the product is {product['status']}"
                    + (
                        f" and ends on {product['termination_date']}"
                        if product["termination_date"]
                        else ""
                    ),
                )
            elif item["product_status"] == "active" and account["state"] != "Active":
                problems[item["id"]] = (
                    "accountNotActive",
                    f"billing account is {account['state']}: only suspensions and cleanup orders",
                )
        if problems:
            result = _fail(conn, order, items, problems, now, today)
        else:
            result = _apply(conn, order, items, account, now, today)
        return commands.complete(conn, command_id, result, now=now)["result"]


def _apply(conn, order, items, account, now, today) -> dict:
    for item in items.values():
        product_id = item["product_id"]
        before = products.get_product(conn, product_id)["status"]
        products.apply_prepaid_status_change(conn, item["id"], now)
        product = products.get_product(conn, product_id)
        status = entitlement.view(conn, product, account, today)["status"]
        products.set_product_status(conn, product_id, status)
        grant = entitlement.latest_grant(conn, product_id)
        if (
            item["product_status"] == "active"
            and grant is not None
            and grant["period"].upper <= today
        ):
            commands.enqueue(
                conn,
                commands.renewal_id(grant["id"]),
                account["id"],
                kind="renewal",
                due_date=grant["period"].upper,
                request={"previous_interval_id": str(grant["id"])},
                now=now,
            )
        if status != before:
            emit.product(conn, product_id, "StateChange", at=now, today=today)
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "completed"))
    orders.set_order_state(
        conn, order["id"], order_transition(order["state"], "completed"), completion_date=now
    )
    _order_event(conn, order, now)
    return {"outcome": "statusChanged"}


def _fail(conn, order, items, problems, now, today) -> dict:
    for item in items.values():
        products.undo_status_change(conn, item["id"])
        if item["id"] in problems:
            code, reason = problems[item["id"]]
            orders.add_error_message(
                conn,
                order_id=order["id"],
                order_item_id=item["id"],
                code=code,
                reason=reason,
                occurred_at=now,
            )
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "failed"))
    orders.set_order_state(
        conn, order["id"], order_transition(order["state"], "failed"), completion_date=now
    )
    _order_event(conn, order, now)
    return {"outcome": "failed", "reasons": sorted(code for code, _ in problems.values())}


def _order_event(conn, order, now) -> None:
    from app.api.ordering import order_body

    emit.emit(
        conn,
        "TMF622",
        "productOrder",
        "StateChange",
        lambda: order_body(conn, orders.get_order(conn, order["id"])),
        at=now,
    )
