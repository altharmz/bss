"""Single-product prepaid posting primitive, called only by the billing owner.

No producer or dispatcher yet. The immutable journal request is the accepted quote;
posting never accepts replacement amounts from a retry. Multi-product order trees
need a separate extension to the one-hold/one-bill conversion foundation.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.range import Range
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.billing import settlement
from app.billing.core import tax_of
from app.billing.model import AppliedTax, DateRange
from app.events import emit
from app.repository import billing, funding, presentation
from app.repository import prepaid_commands as commands
from app.repository._sql import fetch_one, jsonb, returning
from app.worker.db import holds_ownership

Cents = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class TaxQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1)
    rate: Decimal = Field(ge=0, allow_inf_nan=False)
    amount: Cents


class IntervalQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    offering_id: UUID
    price_id: UUID
    currency: str
    amount: Cents
    taxes: list[TaxQuote]
    anchor: date
    index: int = Field(ge=0, strict=True)
    unit: Literal["daily", "weekly", "monthly", "yearly"]
    length: int = Field(ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def valid_taxes(self):
        if len({t.category for t in self.taxes}) != len(self.taxes):
            raise ValueError("duplicate tax category")
        if any(t.amount != tax_of(self.amount, t.rate) for t in self.taxes):
            raise ValueError("quote tax does not match amount and rate")
        return self

    def period(self) -> DateRange:
        def boundary(index):
            if self.unit in {"daily", "weekly"}:
                return self.anchor + timedelta(
                    days=index * self.length * (7 if self.unit == "weekly" else 1)
                )
            months = index * self.length * (12 if self.unit == "yearly" else 1)
            year, month = divmod(self.anchor.year * 12 + self.anchor.month - 1 + months, 12)
            return date(year, month + 1, min(self.anchor.day, monthrange(year, month + 1)[1]))

        return DateRange(boundary(self.index), boundary(self.index + 1))


def post_interval(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Post once in a savepoint; caller commits effects/result/events together.

    Requires an outstanding exact command-owned hold. Failure rolls back posting
    and retains the hold. Technical failure bookkeeping belongs to the dispatcher
    after rollback. A completed retry returns its original result, even days later.
    """
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    if now.tzinfo is None:
        raise ValueError("authorization requires an aware instant")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        if cmd["kind"] not in {"order", "renewal"}:
            raise ValueError("interval posting requires an order or renewal command")
        quote = IntervalQuote.model_validate(cmd["request"])
        period = quote.period()
        if cmd["kind"] == "order":
            if quote.index != 0 or period.start != cmd["due_date"]:
                raise ValueError("initial order quote must start on its due date at index zero")
            # Immediate starts retain their accepted money/cadence after a crash or
            # worker delay. Only actual coverage moves; the journal stays immutable.
            quote = quote.model_copy(update={"anchor": today})
            period = quote.period()
        if period.start != today:
            raise ValueError("funded interval must start on authorization business date")
        account = billing.load_account(conn, cmd["billing_account_id"])
        state = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account.id,))
        if state["rating_type"] != "prepaid" or state["state"] != "Active":
            raise ValueError("authorization requires an active prepaid account")
        if quote.currency != account.currency:
            raise ValueError("quote currency does not match account")
        prod = fetch_one(
            conn, "SELECT * FROM product WHERE id = %s FOR UPDATE", (quote.product_id,)
        )
        if prod is None or prod["billing_account_id"] != account.id:
            raise ValueError("quote product does not belong to account")
        if prod["status"] in {"suspended", "terminated", "cancelled", "aborted"}:
            raise ValueError("product lifecycle prevents authorization")
        if prod["product_offering_id"] != quote.offering_id:
            raise ValueError("offering changes require the lifecycle posting extension")
        if period.start < prod["start_date"] or (
            prod["termination_date"] is not None and prod["termination_date"] <= period.start
        ):
            raise ValueError("product is not eligible on authorization date")
        total = quote.amount + sum((t.amount for t in quote.taxes), Decimal(0))
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
        coverage = Range(period.start, period.end, "[)")
        abr = billing.insert_abr(
            conn,
            product_id=quote.product_id,
            billing_account_id=account.id,
            charge_type="recurring",
            amount=quote.amount,
            currency=account.currency,
            period=coverage,
            billing_run_id=run["id"],
            applied_at=now,
            product_offering_id=quote.offering_id,
            taxes=tuple(AppliedTax(t.category, t.rate, t.amount) for t in quote.taxes),
        )
        bill = billing.insert_bill(
            conn,
            billing_account_id=account.id,
            currency=account.currency,
            bill_period=coverage,
            bill_date=today,
            amount_due=total,
            billing_run_id=run["id"],
            payment_due_date=today,
            run_type="offCycle",
        )
        billing.record_bill_issue(conn, bill["id"], now=now)
        billing.attach_abrs_to_bill(conn, bill["id"], account.id, [abr["id"]])
        candidate = next(
            c for c in billing.load_issue_candidates(conn, account.id) if c.id == bill["id"]
        )
        settlement.validate(candidate, period.end)
        presentation.snapshot_bill(conn, bill["id"], now=now)
        for change in settlement.issue(candidate):
            billing.record_bill_state(conn, change, now=now, cause="run", notify=False)
        emit.bill(conn, bill["id"], "Create", at=now)
        funding.convert_for_command(
            conn, account.id, bill["id"], command_id=command_id, converted_at=now
        )
        grant = returning(
            conn,
            "INSERT INTO funded_interval (product_id, billing_account_id, currency, command_id,"
            " command_kind, billing_run_id, applied_rate_id, authorized_at, period, anchor_date,"
            " interval_index, amount, tax_amount, quote)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                quote.product_id,
                account.id,
                account.currency,
                command_id,
                cmd["kind"],
                run["id"],
                abr["id"],
                now,
                coverage,
                quote.anchor,
                quote.index,
                quote.amount,
                total - quote.amount,
                jsonb(cmd["request"]),
            ),
        )
        result = {
            "outcome": "funded",
            "interval_id": str(grant["id"]),
            "bill_id": str(bill["id"]),
            "applied_rate_id": str(abr["id"]),
            "run_id": str(run["id"]),
        }
        conn.execute(
            "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
            " WHERE id = %s",
            (now, now, run["id"]),
        )
        return commands.complete(conn, command_id, result, now=now)["result"]
