"""Usage prices of pay-as-you-go offerings (docs/decisions/usage.md, "Rating").

A usage price (TMF620 `priceType` `usage`) is the price of one block of its `unitOfMeasure`,
the TMF620 Quantity `{amount, units}`: usage is rounded up to whole blocks and each block
costs the price. The unit names the usage kind and the block in the record unit (data in
bytes, voice in seconds, SMS in messages).
"""

from decimal import Decimal
from typing import Any

# unit -> (kind, size of one unit in the record unit)
UNITS: dict[str, tuple[str, int]] = {
    "KB": ("data", 1024),
    "MB": ("data", 1024**2),
    "GB": ("data", 1024**3),
    "second": ("voice", 1),
    "minute": ("voice", 60),
    "message": ("sms", 1),
}


def parse(unit_of_measure: Any) -> tuple[str, int]:
    """(kind, block in the record unit) of a usage price's unit of measure; ValueError says
    what is wrong."""
    if not isinstance(unit_of_measure, dict):
        raise ValueError("unitOfMeasure is required for a usage price: {amount, units}")
    units, amount = unit_of_measure.get("units"), unit_of_measure.get("amount", 1)
    if units not in UNITS:
        raise ValueError(f"unitOfMeasure.units must be one of {', '.join(UNITS)}")
    if isinstance(amount, bool) or not isinstance(amount, int | float):
        raise ValueError("unitOfMeasure.amount must be a number")
    block = Decimal(str(amount)) * UNITS[units][1]
    if amount <= 0 or block != block.to_integral_value():
        raise ValueError(
            "unitOfMeasure.amount must be positive and a whole number of the record unit"
            " (bytes, seconds or messages)"
        )
    return UNITS[units][0], int(block)
