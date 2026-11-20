"""Deterministic stand-in for the identity check service.

Test values (the family name): `Declined` fails, `Review` stays in review, `Unavailable` fails
temporarily until `recover()`; anything else passes. The first call of a key only records the
check (None: not decided), so the verdict comes on the worker's next wake-up. A repeated key
gets the first answer and counts as one check in `checks`.
"""

from app.integrations.identity.interface import IdentityResult, IdentityTemporary, Person

DECLINED = "Declined"
REVIEW = "Review"
UNAVAILABLE = "Unavailable"


class StubIdentity:
    def __init__(self) -> None:
        self.checks: dict[str, Person] = {}
        self.attempts: list[str] = []  # every call, repeated keys included
        self.recovered = False

    def reset(self) -> None:
        self.checks.clear()
        self.attempts.clear()
        self.recovered = False

    def recover(self) -> None:
        self.recovered = True

    def verify(
        self, key: str, person: Person, address: dict, document: dict | None = None
    ) -> IdentityResult | None:
        self.attempts.append(key)
        if person.family_name == UNAVAILABLE and not self.recovered:
            raise IdentityTemporary("stub: the identity service is unavailable")
        if key not in self.checks:
            self.checks[key] = person
            return None
        if person.family_name == DECLINED:
            return IdentityResult("fail", "stub: the identity could not be confirmed")
        if person.family_name == REVIEW:
            return IdentityResult("review", "stub: the documents need a person to look")
        return IdentityResult("pass")
