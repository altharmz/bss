"""Prepaid purchases that have no interval to fund: an add-on pack and a pay-as-you-go line.

Acceptance (app/ordering/prepaid.py) holds the one-time fees from the balance and retains one
`order` command, `{"order_id", "charges": [...]}`, due today. The billing owner posts it once the
network confirmed (app/worker/prepaid.py gates it like any order): one bill per product with
its fee ABRs (docs/decisions/prepaid.md), the hold converted, the products active and the order
completed, atomically. There is no funded interval, so the products are never renewed.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.range import Range
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.billing import settlement
from app.billing.prepaid import FeeQuote, _complete_order, post_fees
from app.events import emit
from app.repository import balance_actions, billing, funding, orders, presentation
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_one
from app.worker.db import holds_ownership


class Charge(BaseModel):
    """What one new product is charged: its fees. An add-on also names its plan and the
    date its pack ends (the plan's paid-through date)."""

    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    offering_id: UUID
    fees: list[FeeQuote]
    parent_product_id: UUID | None = None
    ends: date | None = None

    @property
    def gross(self) -> Decimal:
        return sum((f.gross for f in self.fees), Decimal(0))


class ChargeOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    charges: list[Charge] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_products(self):
        if len({c.product_id for c in self.charges}) != len(self.charges):
            raise ValueError("duplicate product in order charges")
        return self

    @property
    def total(self) -> Decimal:
        return sum((c.gross for c in self.charges), Decimal(0))


def post_charges(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Post the fees of a pending add-on or pay-as-you-go order and complete it."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        request = ChargeOrder.model_validate(cmd["request"])
        account = billing.load_account(conn, cmd["billing_account_id"])
        balance_actions.release_expired(conn, account.id, now)
        state = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account.id,))
        if (
            cmd["kind"] != "order"
            or state["rating_type"] != "prepaid"
            or state["state"] != "Active"
        ):
            raise ValueError("charges need an order command of an active prepaid account")
        orders.lock_order(conn, request.order_id)
        order = orders.get_order(conn, request.order_id)
        if (
            request.order_id != cmd["id"]
            or order is None
            or order["billing_account_id"] != account.id
            or order["state"] != "inProgress"
            or any(i["action"] != "add" or i["state"] != "inProgress" for i in order["items"])
            or {i["product_id"] for i in order["items"]} != {c.product_id for c in request.charges}
        ):
            raise ValueError("charge command must match its entire pending order")
        total = request.total
        hold = fetch_one(
            conn,
            "SELECT h.* FROM funding_command_hold c JOIN funding_hold h ON h.id = c.hold_id"
            " WHERE c.command_id = %s AND c.billing_account_id = %s",
            (command_id, account.id),
        )
        if total > 0 and (
            hold is None or hold["released_at"] is not None or hold["amount"] != total
        ):
            raise ValueError("posting requires the exact outstanding command hold")
        run = commands.ensure_run(conn, command_id, run_date=today, now=now)
        if run["status"] != "running":
            raise ValueError("command run is not running")
        bills = []
        for charge in sorted(request.charges, key=lambda c: c.product_id):
            prod = fetch_one(
                conn, "SELECT * FROM product WHERE id = %s FOR UPDATE", (charge.product_id,)
            )
            if prod is None or prod["billing_account_id"] != account.id:
                raise ValueError("charged product does not belong to account")
            if prod["status"] in {"terminated", "cancelled", "aborted"}:
                raise ValueError("product lifecycle prevents charging")
            if charge.fees:
                bills.append(_post_bill(conn, account, run, charge, now, today))
        if bills:
            funding.convert_bills_for_command(
                conn, account.id, bills, command_id=command_id, converted_at=now
            )
        elif hold is not None and hold["released_at"] is None:
            funding.release(conn, account.id, hold["id"], released_at=now, command_id=command_id)
        _complete_order(conn, order, now, today)
        conn.execute(
            "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
            " WHERE id = %s",
            (now, now, run["id"]),
        )
        result = {
            "outcome": "charged",
            "run_id": str(run["id"]),
            "bills": [str(b) for b in bills],
        }
        return commands.complete(conn, command_id, result, now=now)["result"]


def _post_bill(conn, account, run, charge: Charge, now: datetime, today: date) -> UUID:
    day = Range(today, today + timedelta(days=1), "[)")
    fee_abrs = post_fees(
        conn, account, run, charge.product_id, charge.offering_id, charge.fees, now, today
    )
    bill = billing.insert_bill(
        conn,
        billing_account_id=account.id,
        currency=account.currency,
        bill_period=day,
        bill_date=today,
        amount_due=charge.gross,
        billing_run_id=run["id"],
        payment_due_date=today,
        run_type="offCycle",
    )
    billing.record_bill_issue(conn, bill["id"], now=now)
    billing.attach_abrs_to_bill(conn, bill["id"], account.id, fee_abrs)
    candidate = next(
        c for c in billing.load_issue_candidates(conn, account.id) if c.id == bill["id"]
    )
    settlement.validate(candidate, day.upper)
    presentation.snapshot_bill(conn, bill["id"], now=now)
    for change in settlement.issue(candidate):
        billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
    emit.bill(conn, bill["id"], "Create", at=now)
    return bill["id"]
