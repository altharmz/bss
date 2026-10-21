"""Atomic prepaid purchase posting, called only by the billing owner.

No producer yet. Immutable requests retain accepted purchase quotes
or renewal predecessor identities; callers never supply replacement retry amounts.
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
from app.billing.core import charge_taxes, price_at, tax_of
from app.billing.model import AppliedTax, DateRange, Product
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


class OrderQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intervals: list[IntervalQuote] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_products(self):
        if len({q.product_id for q in self.intervals}) != len(self.intervals):
            raise ValueError("duplicate product in order quote")
        return self


class RenewalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous_interval_id: UUID


def _renewal_quote(conn, cmd, account, today, tz):
    request = RenewalRequest.model_validate(cmd["request"])
    previous = fetch_one(
        conn, "SELECT * FROM funded_interval WHERE id = %s", (request.previous_interval_id,)
    )
    if previous is None or previous["billing_account_id"] != account.id:
        raise ValueError("renewal requires an owned previous interval")
    prod = fetch_one(
        conn, "SELECT * FROM product WHERE id = %s FOR UPDATE", (previous["product_id"],)
    )
    latest = fetch_one(
        conn,
        "SELECT id FROM funded_interval WHERE product_id = %s ORDER BY upper(period) DESC LIMIT 1",
        (prod["id"],),
    )
    if latest["id"] != previous["id"] or previous["period"].upper != cmd["due_date"]:
        raise ValueError("renewal must follow the latest interval at its paid-through boundary")
    price = price_at(
        Product(
            prod["id"],
            prod["product_offering_id"],
            prod["status"],
            prod["start_date"],
            prod["termination_date"],
        ),
        today,
        billing.load_recurring_prices(conn, [prod["product_offering_id"]]),
        account.currency,
        tz,
    )
    old = IntervalQuote.model_validate(previous["quote"])
    continuous = today == cmd["due_date"] and (old.unit, old.length) == (
        price.recurring_period,
        price.recurring_period_length,
    )
    return IntervalQuote(
        product_id=prod["id"],
        offering_id=price.offering_id,
        price_id=price.id,
        currency=account.currency,
        amount=price.amount,
        taxes=[
            TaxQuote(category=t.category, rate=t.rate, amount=t.amount)
            for t in charge_taxes(price.amount, price, account, today, tz)
        ],
        anchor=previous["anchor_date"] if continuous else today,
        index=previous["interval_index"] + 1 if continuous else 0,
        unit=price.recurring_period,
        length=price.recurring_period_length,
    )


def post_interval(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """Post a single quote or complete initial-order quote set atomically.

    Caller owns the transaction and technical-error bookkeeping after rollback.
    Initial orders retain accepted money and buy coverage from today. Renewals
    remain single-product. Previous-interval requests select today's price/tax
    and reserve funds atomically, including after an outage.
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
        batch = "intervals" in cmd["request"]
        if batch and cmd["kind"] != "order":
            raise ValueError("multi-product posting requires an order command")
        account = billing.load_account(conn, cmd["billing_account_id"])
        state = fetch_one(conn, "SELECT * FROM billing_account WHERE id = %s", (account.id,))
        if state["rating_type"] != "prepaid" or state["state"] != "Active":
            raise ValueError("authorization requires an active prepaid account")
        renewal = cmd["kind"] == "renewal" and "previous_interval_id" in cmd["request"]
        if renewal:
            parsed = [_renewal_quote(conn, cmd, account, today, tz)]
            originals = [parsed[0].model_dump(mode="json")]
        elif batch:
            parsed = OrderQuote.model_validate(cmd["request"]).intervals
            originals = cmd["request"]["intervals"]
        else:
            parsed = [IntervalQuote.model_validate(cmd["request"])]
            originals = [cmd["request"]]
        quotes = sorted(zip(parsed, originals, strict=True), key=lambda q: q[0].product_id)
        accepted = []
        for quote, original_quote in quotes:
            period = quote.period()
            if cmd["kind"] == "order":
                if quote.index != 0 or period.start != cmd["due_date"]:
                    raise ValueError("initial order quote must start on its due date at index zero")
                quote = quote.model_copy(update={"anchor": today})
                period = quote.period()
            if period.start != today:
                raise ValueError("funded interval must start on authorization business date")
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
            accepted.append((quote, period, total, original_quote))
        total = sum((q[2] for q in accepted), Decimal(0))
        if renewal:
            funding.reserve_for_command(conn, account.id, total, command_id=command_id, held_at=now)
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
        purchases = [
            _post_purchase(conn, cmd, account, run, quote, period, total, original, now, today)
            for quote, period, total, original in accepted
        ]
        if batch:
            funding.convert_bills_for_command(
                conn,
                account.id,
                [UUID(p["bill_id"]) for p in purchases],
                command_id=command_id,
                converted_at=now,
            )
            result = {"outcome": "funded", "run_id": str(run["id"]), "purchases": purchases}
        else:
            result = purchases[0]
            funding.convert_for_command(
                conn, account.id, UUID(result["bill_id"]), command_id=command_id, converted_at=now
            )
        conn.execute(
            "UPDATE billing_run SET status = 'completed', finished_at = %s, heartbeat_at = %s"
            " WHERE id = %s",
            (now, now, run["id"]),
        )
        return commands.complete(conn, command_id, result, now=now)["result"]


def _post_purchase(conn, cmd, account, run, quote, period, total, original_quote, now, today):
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
            cmd["id"],
            cmd["kind"],
            run["id"],
            abr["id"],
            now,
            coverage,
            quote.anchor,
            quote.index,
            quote.amount,
            total - quote.amount,
            jsonb(original_quote),
        ),
    )
    return {
        "outcome": "funded",
        "interval_id": str(grant["id"]),
        "bill_id": str(bill["id"]),
        "applied_rate_id": str(abr["id"]),
        "run_id": str(run["id"]),
    }
