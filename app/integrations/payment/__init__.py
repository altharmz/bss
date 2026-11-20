"""The payment gateway adapter (Stripe-style placeholder): the interface and its stub.

`BSS_PAYMENT_ADAPTER` selects the implementation at startup (`stub`, the default); an
unknown value fails startup (app/config.py) and `adapter()`. docs/decisions/payments.md.
"""

from app.config import settings
from app.integrations.payment.interface import (
    Card,
    CardRejected,
    ChargeResult,
    PaymentAdapter,
    PaymentDeclined,
    PaymentTemporary,
)

__all__ = [
    "Card",
    "CardRejected",
    "ChargeResult",
    "PaymentAdapter",
    "PaymentDeclined",
    "PaymentTemporary",
    "adapter",
]

_ADAPTERS: dict[str, PaymentAdapter] = {}


def adapter() -> PaymentAdapter:
    name = settings.payment_adapter
    if name not in _ADAPTERS:
        if name != "stub":
            raise ValueError(f"unknown BSS_PAYMENT_ADAPTER {name!r}")
        from app.integrations.payment.stub import StubPayment

        _ADAPTERS[name] = StubPayment()
    return _ADAPTERS[name]
