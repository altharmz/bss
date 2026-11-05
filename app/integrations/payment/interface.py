"""The contract between the BSS and the payment gateway.

A charge is submitted with an idempotency key (the id of the BSS command): the gateway
must answer a repeated key with the first answer and never charge twice. `charge` only
submits (it returns when the gateway has the charge); the outcome comes later and is read
with `result` (a real gateway also pushes it to a webhook, which records the same result).
`charge` raises `PaymentDeclined` when the gateway refuses at once and `PaymentTemporary`
when the answer is not known (the BSS submits again with the same key).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol


class PaymentDeclined(Exception):
    """The card was declined: final, nothing is charged."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PaymentTemporary(Exception):
    """A transport or temporary error: the same charge is submitted again later."""


@dataclass(frozen=True)
class ChargeResult:
    status: Literal["succeeded", "declined"]
    gateway_ref: str | None = None
    reason: str | None = None


class PaymentAdapter(Protocol):
    def charge(self, key: str, token: str, amount: Decimal, currency: str) -> None: ...

    def result(self, key: str) -> ChargeResult | None:
        """None while the gateway has not decided."""
        ...

    def refund(self, key: str, gateway_ref: str, amount: Decimal) -> str:
        """Refund (part of) a confirmed charge; returns the refund's reference."""
        ...
