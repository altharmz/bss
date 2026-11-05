"""The tax engine adapter: the interface and its stub.

`BSS_TAX_ADAPTER` selects the implementation at startup (`stub`, the default); an unknown
value fails startup (app/config.py) and `adapter()`.
"""

from app.config import settings
from app.integrations.tax.interface import (
    ChargeLine,
    TaxAdapter,
    TaxLine,
    TaxQuoteResult,
    TaxUnavailable,
)

__all__ = ["ChargeLine", "TaxAdapter", "TaxLine", "TaxQuoteResult", "TaxUnavailable", "adapter"]

_ADAPTERS: dict[str, TaxAdapter] = {}


def adapter() -> TaxAdapter:
    name = settings.tax_adapter
    if name not in _ADAPTERS:
        if name != "stub":
            raise ValueError(f"unknown BSS_TAX_ADAPTER {name!r}")
        from app.integrations.tax.stub import StubTax

        _ADAPTERS[name] = StubTax()
    return _ADAPTERS[name]
