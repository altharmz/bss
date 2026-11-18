"""The kind of a prepaid offering (docs/decisions/prepaid.md, "Plans, renewal, add-ons and fees").

Held as a TMF620 `productOfferingCharacteristic` named `offeringKind` on the offering, no
column of its own: one `characteristicValueSpecification` whose value is `plan` (the default
when the characteristic is absent), `addOn` (a one-time pack bought for the rest of a plan's
period) or `payAsYouGo` (no recurring price; usage is rated by a later slice). Only a plan
has a recurring price.
"""

from typing import Any

NAME = "offeringKind"
# an add-on pass of a fixed number of days (#200): it ends after them, or with the plan period
PASS_DAYS = "passDays"  # noqa: S105 - a characteristic name, not a secret
MAX_PASS_DAYS = 30
PLAN, ADD_ON, PAY_AS_YOU_GO = "plan", "addOn", "payAsYouGo"
KINDS = (PLAN, ADD_ON, PAY_AS_YOU_GO)


def _entry(details: dict[str, Any] | None, name: str = NAME) -> dict[str, Any] | None:
    for entry in (details or {}).get("productOfferingCharacteristic") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _value(entry: dict[str, Any]) -> Any:
    values = [v.get("value") for v in entry.get("characteristicValueSpecification") or []]
    return values[0] if len(values) == 1 else None


def problems(details: dict[str, Any] | None) -> list[str]:
    found = []
    entry = _entry(details)
    if entry is not None and _value(entry) not in KINDS:
        found.append(f"{NAME}: one value of {list(KINDS)} is expected")
    days = _entry(details, PASS_DAYS)
    if days is not None:
        value = _value(days)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PASS_DAYS:
            found.append(f"{PASS_DAYS}: one whole number of days from 1 to {MAX_PASS_DAYS}")
        if kind_of(details) != ADD_ON:
            found.append(f"{PASS_DAYS}: only an addOn offering is a pass of a number of days")
    return found


def pass_days(details: dict[str, Any] | None) -> int | None:
    """The days an add-on pass lasts; None: it lasts the rest of its plan's period."""
    entry = _entry(details, PASS_DAYS)
    value = None if entry is None else _value(entry)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def kind_of(details: dict[str, Any] | None) -> str:
    entry = _entry(details)
    value = None if entry is None else _value(entry)
    return value if value in KINDS else PLAN
