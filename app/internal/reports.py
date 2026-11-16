"""Prepaid KPIs by month, under /internal/v1/report. Ours, not TMF: TMF has no reporting
API for business KPIs (docs/decisions/reporting.md, issue #198).

`GET /internal/v1/report/kpi?from=YYYY-MM&to=YYYY-MM&currency=` : per calendar month of
business dates, for the prepaid accounts of one currency, the lines (opening, gross adds,
disconnects, net adds, closing, average), churn, tax-excluded revenue by source and ARPU.
Computed on request from products and ABRs; nothing is stored.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from fastapi import APIRouter, Request

from app.api.common import money
from app.api.deps import ClockDep, Tx
from app.api.errors import bad_request
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.repository import reports

BASE = "/internal/v1/report"
router = APIRouter(prefix=BASE)

MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
MAX_MONTHS = 24
DEFAULT_MONTHS = 12
SOURCES = ("plans", "addOns", "usage", "fees")
CENT = Decimal("0.01")


def _month(request: Request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    found = MONTH.match(raw)
    if found is None:
        raise bad_request(f"invalid value for {name}: expected YYYY-MM")
    return date(int(found[1]), int(found[2]), 1)


def _shift(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _percent(part: int, whole: int) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(part) * 100 / whole).quantize(CENT, ROUND_HALF_EVEN)


@router.get("/kpi")
def kpi_report(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    for name in request.query_params:
        if name not in ("from", "to", "currency"):
            raise bad_request(f"unknown query parameter {name}")
    today = clock.today()
    current = today.replace(day=1)
    to = _month(request, "to") or current
    start = _month(request, "from") or _shift(to, 1 - DEFAULT_MONTHS)
    if to > current:
        raise bad_request("to is after the current business month")
    if start > to:
        raise bad_request("from is after to")
    if _shift(start, MAX_MONTHS) <= to:
        raise bad_request(f"at most {MAX_MONTHS} months")
    currency = request.query_params.get("currency", settings.ui_currency)
    if not CURRENCY.match(currency):
        raise bad_request("invalid value for currency")

    last = min(_shift(to, 1) - timedelta(days=1), today)
    with tx() as conn:
        opening, moves = reports.line_movements(conn, currency, start, last)
        amounts = reports.revenue(conn, currency, start, last)

    months: list[dict[str, Any]] = []
    month = start
    while month <= to:
        adds = moves.get(("add", month), 0)
        disconnects = moves.get(("disconnect", month), 0)
        closing = opening + adds - disconnects
        average = Decimal(opening + closing) / 2
        split = {source: amounts.get((source, month), Decimal("0.00")) for source in SOURCES}
        total = sum(split.values(), Decimal("0.00"))
        arpu = None if average == 0 else (total / average).quantize(CENT, ROUND_HALF_EVEN)
        month_last = min(_shift(month, 1) - timedelta(days=1), today)
        months.append(
            {
                "month": f"{month:%Y-%m}",
                "firstDay": month,
                "lastDay": month_last,
                "complete": month_last < today,
                "subscribers": {
                    "opening": opening,
                    "grossAdds": adds,
                    "disconnects": disconnects,
                    "netAdds": adds - disconnects,
                    "closing": closing,
                    "average": average,
                },
                "churnRate": _percent(disconnects, opening),
                "revenue": {
                    "total": money(total, currency),
                    **{source: money(split[source], currency) for source in SOURCES},
                },
                "arpu": None if arpu is None else money(arpu, currency),
            }
        )
        opening = closing
        month = _shift(month, 1)
    return TmfJSONResponse(
        {
            "currency": currency,
            "businessDate": today,
            "from": f"{start:%Y-%m}",
            "to": f"{to:%Y-%m}",
            "months": months,
        }
    )
