"""The device (IMEI) of a line: format rules and what an order item says about it
(docs/decisions/network.md#device-issue-194).

An IMEI is 15 digits whose last digit is the Luhn check digit. It travels as the
`imei` TMF637 `productCharacteristic` of an add item (activation) or of a modify item that
carries nothing else (a device change).
"""

import re
from typing import Any

CHARACTERISTIC = "imei"
COMPATIBILITY = "deviceCompatibility"
REASONS = "deviceReasons"
SHAPE = re.compile(r"[0-9]{15}")


def valid(imei: Any) -> bool:
    """15 digits with a correct Luhn check digit."""
    if not isinstance(imei, str) or not SHAPE.fullmatch(imei):
        return False
    total = 0
    for position, digit in enumerate(reversed(imei)):
        value = int(digit)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def problem(imei: Any) -> str | None:
    """Why this is not an IMEI, or None."""
    if not isinstance(imei, str) or not SHAPE.fullmatch(imei):
        return "an IMEI is exactly 15 digits"
    if not valid(imei):
        return "the IMEI check digit is wrong (Luhn)"
    return None


def requested(details: dict[str, Any] | None) -> str | None:
    """The `imei` characteristic value an item's documents carry, if any."""
    entries = ((details or {}).get("product") or {}).get("productCharacteristic") or []
    for entry in entries:
        if entry.get("name") == CHARACTERISTIC:
            return entry.get("value")
    return None
