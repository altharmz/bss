"""Prepaid termination credits, posted only by the billing owner.

A termination is effective on its requested business date and excludes that day.
Each grant still covering that date credits its unused dates: the charge's net for
the used dates is rounded once, as a charge is, and the credit is the difference,
tax lines likewise (the postpaid refund rule). A charge is credited at most once.
The credit ABR, its settled offCycle credit note (the spendable source) and the
completed order commit with the command result.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.range import Range
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.billing import engine_tax, entitlement, settlement
from app.billing.core import prorate, refund_taxes
from app.billing.model import DateRange
from app.events import emit
from app.repository import billing, orders, presentation, products
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_all, fetch_one
from app.worker.db import holds_ownership


class TerminationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    terminations: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_products(self):
        if len(set(self.terminations)) != len(self.terminations):
            raise ValueError("duplicate terminated product")
        return self


def credit(charge: dict, period: DateRange, ends: date) -> tuple[Decimal, tuple] | None:
    """The credit of a grant `period` charged `charge`, terminated on `ends`: net and
    tax lines (negative), or None when nothing is unused."""
    used = min(max((ends - period.start).days, 0), period.days)
    expected = prorate(charge["amount"], used, period)
    net = expected - charge["amount"]
    if net == 0:
        return None
    return net, refund_taxes(billing.applied_taxes(charge), expected, charge["amount"])


def post_termination(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Credit, end and complete a termination order's products atomically."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        request = TerminationRequest.model_validate(cmd["request"])
        account = billing.load_account(conn, cmd["billing_account_id"])
        state = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account.id,))
        if cmd["kind"] != "order" or state["rating_type"] != "prepaid":
            raise ValueError("termination requires a prepaid order command")
        orders.lock_order(conn, request.order_id)
        order = orders.get_order(conn, request.order_id)
        locked = products.lock_products(conn, request.terminations)
        items = {i["id"]: i for i in order["items"]} if order else {}
        if (
            request.order_id != cmd["id"]
            or order is None
            or order["billing_account_id"] != account.id
            or order["state"] != "inProgress"
            or any(i["action"] != "delete" or i["state"] != "inProgress" for i in items.values())
            or {i["product_id"] for i in items.values()} != set(request.terminations)
            or any(
                p["termination_date"] != cmd["due_date"] or p["terminated_by_item_id"] not in items
                for p in locked.values()
            )
        ):
            raise ValueError("termination command must match its entire pending order")
        run = None
        credits = []
        for product_id in sorted(request.terminations):
            for grant in fetch_all(
                conn,
                "SELECT * FROM funded_interval WHERE product_id = %s"
                " AND upper(period) > %s ORDER BY lower(period)",
                (product_id, cmd["due_date"]),
            ):
                if run is None:
                    run = commands.ensure_run(conn, command_id, run_date=today, now=now)
                posted = credit_grant(conn, account, run, grant, cmd["due_date"], now, today)
                if posted is not None:
                    credits.append(posted)
        if run is not None:
            conn.execute(
                "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
                " WHERE id = %s",
                (now, now, run["id"]),
            )
        # the add-on packs of an ended plan end with it
        conn.execute(
            "UPDATE product SET termination_date = %s,"
            " status = CASE WHEN %s <= %s THEN 'terminated' ELSE status END::product_status_type"
            " WHERE parent_product_id = ANY(%s)"
            " AND (termination_date IS NULL OR termination_date > %s)",
            (cmd["due_date"], cmd["due_date"], today, list(request.terminations), cmd["due_date"]),
        )
        _complete(conn, order, state, now, today)
        result = {"outcome": "terminated", "credits": credits}
        if run is not None:
            result["run_id"] = str(run["id"])
        return commands.complete(conn, command_id, result, now=now)["result"]


def credit_grant(conn, account, run, grant, ends, now, today):
    charge = billing.get_abr(conn, grant["applied_rate_id"])
    if fetch_one(
        conn,
        "SELECT id FROM applied_customer_billing_rate WHERE refunds_rate_id = %s",
        (charge["id"],),
    ):
        return None  # already credited: nothing is left uncredited
    period = DateRange(grant["period"].lower, grant["period"].upper)
    found = credit(charge, period, ends)
    if found is None:
        return None
    net, taxes = found
    coverage = Range(max(ends, period.start), period.end, "[)")
    abr = billing.insert_abr(
        conn,
        product_id=grant["product_id"],
        billing_account_id=account.id,
        charge_type="refund",
        amount=net,
        currency=account.currency,
        period=coverage,
        billing_run_id=run["id"],
        applied_at=now,
        refunds_rate_id=charge["id"],
        product_offering_id=charge["product_offering_id"],
        taxes=taxes,
    )
    if charge["amount"] + net == 0:  # the whole charge is credited: its engine tax is void
        engine_tax.void(conn, charge["id"], now=now)
    # A credit note is dated when it is posted; a late worker still credits from `ends`.
    end = max(period.end, today + timedelta(days=1))
    bill = billing.insert_bill(
        conn,
        billing_account_id=account.id,
        currency=account.currency,
        bill_period=Range(today, end, "[)"),
        bill_date=today,
        amount_due=billing.abr_total(abr),
        billing_run_id=run["id"],
        payment_due_date=today,
        run_type="offCycle",
    )
    billing.record_bill_issue(conn, bill["id"], now=now)
    billing.attach_abrs_to_bill(conn, bill["id"], account.id, [abr["id"]])
    candidate = next(
        c for c in billing.load_issue_candidates(conn, account.id) if c.id == bill["id"]
    )
    settlement.validate(candidate, end)
    presentation.snapshot_bill(conn, bill["id"], now=now)
    for change in settlement.issue(candidate):
        billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
    emit.bill(conn, bill["id"], "Create", at=now)
    return {
        "product_id": str(grant["product_id"]),
        "interval_id": str(grant["id"]),
        "charge_id": str(charge["id"]),
        "applied_rate_id": str(abr["id"]),
        "bill_id": str(bill["id"]),
    }


def _complete(conn, order, account, now, today):
    from app.api.ordering import order_body
    from app.ordering.states import item_transition, order_transition

    for item in order["items"]:
        before = products.get_product(conn, item["product_id"])
        status = entitlement.view(conn, before, account, today)["status"]
        if status != before["status"]:
            products.set_product_status(conn, item["product_id"], status)
            emit.product(conn, item["product_id"], "StateChange", at=now, today=today)
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
