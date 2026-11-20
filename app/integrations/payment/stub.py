"""Deterministic stand-in for the payment gateway.

Test values (the card token): `tok_decline` is declined, `tok_fail` fails temporarily
until `recover()` is called for it; any other token succeeds. Saving a card from a token alone
(`card`, self-care) knows only the documented test tokens of `CARDS`: `tok_visa`,
`tok_mastercard`, `tok_decline` and `tok_fail`; any other token is rejected. The answer to a key is
remembered, so a repeated key gets the first answer and counts as one charge in `charges`.
The result of a charge is available from `result`; the BSS reads it on the worker's next
wake-up after the charge was submitted.
"""

from decimal import Decimal

from app.integrations.payment.interface import Card, CardRejected, ChargeResult, PaymentTemporary

DECLINE_TOKEN = "tok_decline"  # noqa: S105
FAIL_TOKEN = "tok_fail"  # noqa: S105
# the test cards a token stands for (no expiry: the stub's cards never expire)
CARDS = {
    "tok_visa": Card("visa", "4242"),
    "tok_mastercard": Card("mastercard", "4444"),
    DECLINE_TOKEN: Card("visa", "0002"),
    FAIL_TOKEN: Card("visa", "0341"),
}


class StubPayment:
    def __init__(self) -> None:
        self.charges: dict[str, tuple[str, Decimal, str]] = {}
        self.attempts: list[str] = []  # every call to charge, repeated keys included
        self.refunds: dict[str, tuple[str, Decimal]] = {}
        self.recovered: set[str] = set()

    def reset(self) -> None:
        self.charges.clear()
        self.attempts.clear()
        self.refunds.clear()
        self.recovered.clear()

    def recover(self, token: str) -> None:
        self.recovered.add(token)

    def card(self, token: str) -> Card:
        if token not in CARDS:
            raise CardRejected("stub: unknown test token")
        return CARDS[token]

    def charge(self, key: str, token: str, amount: Decimal, currency: str) -> None:
        self.attempts.append(key)
        if key in self.charges:
            return
        if token == FAIL_TOKEN and token not in self.recovered:
            raise PaymentTemporary(f"stub: the gateway is unavailable for {token}")
        self.charges[key] = (token, amount, currency)

    def result(self, key: str) -> ChargeResult | None:
        if key not in self.charges:
            return None
        if self.charges[key][0] == DECLINE_TOKEN:
            return ChargeResult("declined", reason="card_declined: the card was declined")
        return ChargeResult("succeeded", gateway_ref=f"stub_ch_{key}")

    def refund(self, key: str, gateway_ref: str, amount: Decimal) -> str:
        self.refunds.setdefault(key, (gateway_ref, amount))
        return f"stub_re_{key}"
