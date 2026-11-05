"""The kind of a prepaid offering (docs/decisions/prepaid.md, "Plans, renewal, add-ons and fees").

Held as a TMF620 `productOfferingCharacteristic` named `offeringKind` on the offering, no
column of its own: one `characteristicValueSpecification` whose value is `plan` (the default
when the characteristic is absent), `addOn` (a one-time pack bought for the rest of a plan's
period) or `payAsYouGo` (no recurring price; usage is rated by a later slice). Only a plan
has a recurring price.
"""

from typing import Any

NAME = "offeringKind"
PLAN, ADD_ON, PAY_AS_YOU_GO = "plan", "addOn", "payAsYouGo"
KINDS = (PLAN, ADD_ON, PAY_AS_YOU_GO)


def _entry(details: dict[str, Any] | None) -> dict[str, Any] | None:
    for entry in (details or {}).get("productOfferingCharacteristic") or []:
        if isinstance(entry, dict) and entry.get("name") == NAME:
            return entry
    return None


def _value(entry: dict[str, Any]) -> Any:
    values = [v.get("value") for v in entry.get("characteristicValueSpecification") or []]
    return values[0] if len(values) == 1 else None


def problems(details: dict[str, Any] | None) -> list[str]:
    entry = _entry(details)
    if entry is None or _value(entry) in KINDS:
        return []
    return [f"{NAME}: one value of {list(KINDS)} is expected"]


def kind_of(details: dict[str, Any] | None) -> str:
    entry = _entry(details)
    value = None if entry is None else _value(entry)
    return value if value in KINDS else PLAN
