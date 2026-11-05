"""Deterministic tax engine for development and tests.

Per charge: the state's sales/telecom tax (a percent from RATES), the federal USF
(FEDERAL_USF percent) and the state's prepaid wireless 911 fee (a fixed amount from E911,
charged once per charge above zero). ZIP code FAILING_ZIP always raises `TaxUnavailable`.
`committed` and `voided` record what the BSS sent (quote id -> key), for tests.
"""

import uuid
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from app.integrations.tax.interface import ChargeLine, TaxLine, TaxQuoteResult, TaxUnavailable

FAILING_ZIP = "00000"
RATES = {  # state -> percent
    "CA": Decimal("7.25"),
    "NY": Decimal("4.00"),
    "TX": Decimal("6.25"),
    "WA": Decimal("6.50"),
    "FL": Decimal("6.00"),
    "OR": Decimal("0"),
}
DEFAULT_RATE = Decimal("5.00")
FEDERAL_USF = Decimal("3.00")  # percent
E911 = {"CA": Decimal("0.80"), "NY": Decimal("1.20"), "TX": Decimal("0.50")}
DEFAULT_E911 = Decimal("0.75")
CENT = Decimal("0.01")


def _percent(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate / 100).quantize(CENT, rounding=ROUND_HALF_EVEN)


class StubTax:
    def __init__(self) -> None:
        self.committed: dict[str, str] = {}
        self.voided: dict[str, str] = {}

    def reset(self) -> None:
        self.committed.clear()
        self.voided.clear()

    def quote(self, charges: list[ChargeLine], address: dict[str, str], on: date) -> TaxQuoteResult:
        if str(address.get("postCode", "")).split("-")[0] == FAILING_ZIP:
            raise TaxUnavailable("stub: tax engine unavailable")
        state = address["stateOrProvince"]
        lines = []
        for charge in charges:
            taxes = [
                TaxLine(
                    f"{state} state",
                    "sales_tax",
                    RATES.get(state, DEFAULT_RATE),
                    _percent(charge.amount, RATES.get(state, DEFAULT_RATE)),
                ),
                TaxLine(
                    "US federal", "federal_usf", FEDERAL_USF, _percent(charge.amount, FEDERAL_USF)
                ),
            ]
            if charge.amount > 0:
                taxes.append(
                    TaxLine(
                        f"{state} state",
                        "prepaid_wireless_e911",
                        Decimal("0"),
                        E911.get(state, DEFAULT_E911),
                    )
                )
            lines.append(tuple(taxes))
        return TaxQuoteResult(f"stub-{uuid.uuid4()}", tuple(lines))

    def commit(self, key: str, quote_id: str) -> None:
        self.committed.setdefault(key, quote_id)

    def void(self, key: str, quote_id: str) -> None:
        self.voided.setdefault(key, quote_id)
