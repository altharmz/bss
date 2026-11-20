"""The contract between the BSS and the identity check service.

A check is sent with an idempotency key (the id of the BSS `identity_check`): the service must
answer a repeated key with the first answer and never check twice. `verify` submits the check
the first time and is asked again with the same key until it decides: None while it has not,
then `pass`, `fail` with a reason or `review` (a person must look). It raises
`IdentityTemporary` when the answer is not known (the BSS asks again later, same key).
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol


class IdentityTemporary(Exception):
    """A transport or temporary error: the same check is sent again later."""


@dataclass(frozen=True)
class Person:
    given_name: str
    family_name: str
    birth_date: date


@dataclass(frozen=True)
class IdentityResult:
    verdict: Literal["pass", "fail", "review"]
    reason: str | None = None


class IdentityAdapter(Protocol):
    def verify(
        self, key: str, person: Person, address: dict, document: dict | None = None
    ) -> IdentityResult | None:
        """The verdict for the check `key`, or None while the service has not decided."""
        ...
