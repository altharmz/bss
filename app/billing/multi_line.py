"""Multi-line discounts of prepaid plans (docs/decisions/prepaid.md, "Multi-line discounts").

A TMF620 `discount` price of a plan offering whose `unitOfMeasure` is `{amount: N, units:
"lines"}` is a tier: each plan line of an account with at least N live plan lines pays the
price less per period. The tier is chosen at every interval quote (purchase, renewal,
mid-period offering change) with the line count on the quote's start date and never changes
during the period. The charged amount is the recurring price less the discount, at least 0,
and is taxed after the discount.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.billing.core import midnight
from app.repository._sql import fetch_one

LINES = "lines"


class DiscountQuote(BaseModel):
    """The tier an interval was quoted with: its price and what it took off (never more than
    the recurring price)."""

    model_config = ConfigDict(extra="forbid")
    price_id: UUID
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


def lines_of(unit_of_measure: Any) -> int | None:
    """N of a `lines` unit of measure; None for no unit or another unit. ValueError when the
    unit is `lines` but N is not a whole number of 2 or more."""
    if not isinstance(unit_of_measure, dict) or unit_of_measure.get("units") != LINES:
        return None
    amount = unit_of_measure.get("amount")
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int | float)
        or amount != int(amount)
        or amount < 2
    ):
        raise ValueError(
            "unitOfMeasure.amount of a multi-line discount is the minimum number of lines:"
            " a whole number of 2 or more"
        )
    return int(amount)


def live_plan_lines(conn: psycopg.Connection, account_id: UUID, day: date) -> int:
    """The account's live plan lines on `day`: started, not ended, not cancelled; add-ons and
    pay-as-you-go lines (grantless) do not count, suspended lines do (they hold a number)."""
    row = fetch_one(
        conn,
        "SELECT count(*) AS n FROM product WHERE billing_account_id = %s AND NOT grantless"
        " AND parent_product_id IS NULL AND start_date <= %s"
        " AND (termination_date IS NULL OR termination_date > %s)"
        " AND status NOT IN ('cancelled', 'aborted')",
        (account_id, day, day),
    )
    return row["n"] if row else 0


def tier(
    conn: psycopg.Connection,
    *,
    offering_id: UUID,
    currency: str,
    period: str,
    lines: int,
    day: date,
    tz: ZoneInfo,
) -> tuple[UUID, Decimal] | None:
    """(price id, amount) of the offering's tier with the largest N <= `lines`, valid at the
    midnight of `day`, in the currency and for the plan's period; None below every tier."""
    if lines < 2:
        return None
    row = fetch_one(
        conn,
        "SELECT id, amount FROM product_offering_price WHERE product_offering_id = %s"
        " AND price_type = 'discount' AND alteration_lines <= %s AND currency = %s"
        " AND recurring_period = %s AND amount > 0 AND valid_for @> %s::timestamptz"
        " ORDER BY alteration_lines DESC LIMIT 1",
        (offering_id, lines, currency, period, midnight(day, tz)),
    )
    return None if row is None else (row["id"], row["amount"])


def discounted(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    offering_id: UUID,
    currency: str,
    period: str,
    amount: Decimal,
    day: date,
    tz: ZoneInfo,
    extra_lines: int = 0,
) -> tuple[Decimal, DiscountQuote | None]:
    """The net recurring amount of an interval starting on `day` and its discount.
    `extra_lines`: plan lines of the same purchase that do not exist yet (a purchase, and its
    first-charge preview, count the lines being bought)."""
    lines = live_plan_lines(conn, account_id, day) + extra_lines
    found = tier(
        conn,
        offering_id=offering_id,
        currency=currency,
        period=period,
        lines=lines,
        day=day,
        tz=tz,
    )
    if found is None:
        return amount, None
    off = min(found[1], amount)
    if off <= 0:
        return amount, None
    return amount - off, DiscountQuote(price_id=found[0], amount=off)


SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def note(amount: Decimal, currency: str) -> str:
    """ "multi-line discount −$5.00"."""
    symbol = SYMBOLS.get(currency)
    off = f"{symbol}{amount:.2f}" if symbol else f"{amount:.2f} {currency}"
    return f"multi-line discount −{off}"


def label(name: str, amount: Decimal, currency: str) -> str:
    """ "Plan X (multi-line discount −$5.00)": a charge line with its discount."""
    return f"{name} ({note(amount, currency)})"
