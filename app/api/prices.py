"""Read-only TMF monthly price views; the billing engine remains authoritative."""

from decimal import Decimal
from typing import Any

from app.api.common import money, ref
from app.billing.core import tax_of
from app.billing.model import Tax

CATALOG_BASE = "/tmf-api/productCatalogManagement/v5"


def tax_on(amount: Decimal, taxes: tuple[Tax, ...]) -> Decimal:
    """The tax of a full month of `amount`: each tax item rounded once, as billing does.
    The view ignores the account's exemptions; the ABRs show what was applied."""
    return sum((tax_of(amount, t.rate) for t in taxes), Decimal("0.00"))


def total_rate(taxes: tuple[Tax, ...]) -> Decimal:
    return sum((t.rate for t in taxes), Decimal(0))


def monthly_price(
    amount: Decimal,
    currency: str,
    *,
    resource_type: str,
    price_id: Any = None,
    name: str = "Monthly subscription",
    tax: Decimal = Decimal("0.00"),
    tax_rate: Decimal | None = Decimal(0),
) -> dict[str, Any]:
    """`tax`: the tax on `amount`; `tax_rate`: Price.taxRate in percent (None: omitted -
    a total over items taxed at different rates has no one rate)."""
    body = {
        "name": name,
        "description": "Full monthly recurring fee; actual charges are prorated by billing.",
        "priceType": "recurring",
        "recurringChargePeriod": "month",
        "price": {
            "dutyFreeAmount": money(amount, currency),
            "taxIncludedAmount": money(amount + tax, currency),
            **({} if tax_rate is None else {"taxRate": tax_rate}),
            "@type": "Price",
        },
        "@type": resource_type,
    }
    if price_id is not None:
        body["productOfferingPrice"] = ref(
            "ProductOfferingPrice",
            f"{CATALOG_BASE}/productOfferingPrice/{price_id}",
            price_id,
            name,
        )
    return body
