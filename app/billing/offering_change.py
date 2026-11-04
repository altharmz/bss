"""Mid-period prepaid offering changes, posted only by the billing owner.

Acceptance (app/ordering/prepaid.py) holds the new offering's first interval, quoted
from the change date D, and retains the order inProgress with one `order` command due
on D. Posting then, in one transaction: credits the old grant's unused dates [D, paid
through) as a termination credit does (app/billing/termination.py), converts the hold
and posts the new interval from D as a grant that supersedes the old one, announces the
offering change and completes the items and the order. Items of the same order that
change at the renewal boundary keep their retained change and just complete.
Any failure rolls everything back; the hold and the pending tree stay for a retry.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.billing import entitlement
from app.billing.prepaid import IntervalQuote, _post_purchase
from app.billing.termination import credit_grant
from app.events import emit
from app.ordering.states import item_transition, order_transition
from app.repository import balance_actions, billing, funding, orders, products
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_one
from app.worker.db import holds_ownership


class OfferingChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    offering_changes: list[IntervalQuote] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_products(self):
        if len({q.product_id for q in self.offering_changes}) != len(self.offering_changes):
            raise ValueError("duplicate product in offering change")
        return self


def post_offering_change(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Credit, switch and complete a mid-period offering change order atomically."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        request = OfferingChangeRequest.model_validate(cmd["request"])
        day = cmd["due_date"]
        account = billing.load_account(conn, cmd["billing_account_id"])
        balance_actions.release_expired(conn, account.id, now)
        state = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account.id,))
        if cmd["kind"] != "order" or state["rating_type"] != "prepaid":
            raise ValueError("offering change requires a prepaid order command")
        if state["state"] != "Active":
            raise ValueError("offering change requires an active prepaid account")
        orders.lock_order(conn, request.order_id)
        order = orders.get_order(conn, request.order_id)
        items = {i["id"]: i for i in order["items"]} if order else {}
        locked = products.lock_products(conn, [i["product_id"] for i in items.values()])
        quoted = {q.product_id: q for q in request.offering_changes}
        if (
            request.order_id != cmd["id"]
            or order is None
            or order["billing_account_id"] != account.id
            or order["state"] != "inProgress"
            or any(
                i["action"] != "modify"
                or i["state"] != "inProgress"
                or i["product_offering_id"] is None
                for i in items.values()
            )
            or not set(quoted) <= {i["product_id"] for i in items.values()}
        ):
            raise ValueError("offering change command must match its entire pending order")
        for product_id, quote in quoted.items():
            product = locked[product_id]
            item = next(i for i in items.values() if i["product_id"] == product_id)
            change = next(
                (
                    c
                    for c in products.changes_of(conn, product_id)
                    if c["changed_by_item_id"] == item["id"]
                ),
                None,
            )
            if (
                quote.index != 0
                or quote.anchor != day
                or quote.period().start != day
                or quote.currency != account.currency
                or quote.offering_id != item["product_offering_id"]
                or change is None
                or change["change_date"] != day
                or change["effect_announced_at"] is not None
                or products.offering_on(conn, product_id, day) != quote.offering_id
                or product["status"] in {"terminated", "cancelled", "aborted"}
                or product["termination_date"] is not None
            ):
                raise ValueError("offering change quote does not match the pending change")
        total = sum(
            (q.amount + sum((t.amount for t in q.taxes), Decimal(0)) for q in quoted.values()),
            Decimal(0),
        )
        hold = fetch_one(
            conn,
            "SELECT h.* FROM funding_command_hold c JOIN funding_hold h ON h.id = c.hold_id"
            " WHERE c.command_id = %s AND c.billing_account_id = %s",
            (command_id, account.id),
        )
        if hold is None or hold["released_at"] is not None or hold["amount"] != total:
            raise ValueError("posting requires the exact outstanding command hold")
        run = commands.ensure_run(conn, command_id, run_date=today, now=now)
        if run["status"] != "running":
            raise ValueError("command run is not running")
        credits, purchases = [], []
        for quote in sorted(quoted.values(), key=lambda q: q.product_id):
            old = entitlement.latest_grant(conn, quote.product_id)
            if old is None or not old["period"].lower <= day < old["period"].upper:
                raise ValueError("the change date is not inside the product's paid interval")
            posted = credit_grant(conn, account, run, old, day, now, today)
            if posted is not None:
                credits.append(posted)
            amount = quote.amount + sum((t.amount for t in quote.taxes), Decimal(0))
            purchases.append(
                _post_purchase(
                    conn,
                    cmd,
                    account,
                    run,
                    quote,
                    quote.period(),
                    amount,
                    quote.model_dump(mode="json"),
                    now,
                    today,
                    supersedes=old["id"],
                )
            )
        funding.convert_bills_for_command(
            conn,
            account.id,
            [UUID(p["bill_id"]) for p in purchases],
            command_id=command_id,
            converted_at=now,
        )
        _complete(conn, order, state, quoted, now, today)
        conn.execute(
            "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
            " WHERE id = %s",
            (now, now, run["id"]),
        )
        result = {
            "outcome": "offeringChanged",
            "run_id": str(run["id"]),
            "credits": credits,
            "purchases": purchases,
        }
        return commands.complete(conn, command_id, result, now=now)["result"]


def _complete(conn, order, account, quoted, now, today):
    from app.api.ordering import order_body

    for item in order["items"]:
        product_id = item["product_id"]
        if product_id in quoted:
            conn.execute(
                "UPDATE product_offering_change SET effect_announced_at = %s"
                " WHERE changed_by_item_id = %s AND effect_announced_at IS NULL",
                (now, item["id"]),
            )
            emit.product(conn, product_id, "AttributeValueChange", at=now, today=today)
        before = products.get_product(conn, product_id)
        status = entitlement.view(conn, before, account, today)["status"]
        if status != before["status"]:
            products.set_product_status(conn, product_id, status)
            emit.product(conn, product_id, "StateChange", at=now, today=today)
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "completed"))
    orders.set_order_state(
        conn, order["id"], order_transition(order["state"], "completed"), completion_date=now
    )
    emit.emit(
        conn,
        "TMF622",
        "productOrder",
        "StateChange",
        lambda: order_body(conn, orders.get_order(conn, order["id"])),
        at=now,
    )
