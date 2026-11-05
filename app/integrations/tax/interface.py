"""The contract between the BSS and a tax engine (modelled on Avalara AvaTax for
Communications, vendor-neutral).

`quote` computes the taxes of charges and keeps nothing at the engine. `commit` records a
quoted transaction when the charge is booked and `void` reverses it; both take the BSS's
idempotency key, and the engine must answer a repeated key with the first answer. Any of the
three raises `TaxUnavailable` when the engine cannot answer: the caller retries later and
books nothing (docs/decisions/taxes.md).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


class TaxUnavailable(Exception):
    """The engine could not be reached or refused to answer: try again later."""


@dataclass(frozen=True)
class ChargeLine:
    amount: Decimal  # net, before tax
    product_code: str
    transaction_type: str  # recurring | one_time | usage


@dataclass(frozen=True)
class TaxLine:
    jurisdiction: str  # who levies it, e.g. "CA state", "US federal"
    tax_type: str  # sales_tax, telecom_tax, federal_usf, e911_fee, prepaid_wireless_e911
    rate: Decimal  # percent; 0 for a fixed fee
    amount: Decimal


@dataclass(frozen=True)
class TaxQuoteResult:
    quote_id: str
    lines: tuple[tuple[TaxLine, ...], ...]  # taxes of each charge, in order


class TaxAdapter(Protocol):
    def quote(
        self, charges: list[ChargeLine], address: dict[str, str], on: date
    ) -> TaxQuoteResult: ...

    def commit(self, key: str, quote_id: str) -> None: ...

    def void(self, key: str, quote_id: str) -> None: ...
