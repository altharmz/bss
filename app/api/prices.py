"""Read-only TMF monthly price views; the billing engine remains authoritative."""

from decimal import Decimal
from typing import Any

from app.api.common import money, ref

CATALOG_BASE = "/tmf-api/productCatalogManagement/v5"


def monthly_price(
    amount: Decimal,
    currency: str,
    *,
    resource_type: str,
    price_id: Any = None,
    name: str = "Monthly subscription",
) -> dict[str, Any]:
    value = money(amount, currency)
    body = {
        "name": name,
        "description": "Full monthly recurring fee; actual charges are prorated by billing.",
        "priceType": "recurring",
        "recurringChargePeriod": "month",
        "price": {
            "dutyFreeAmount": value,
            "taxIncludedAmount": value,
            "taxRate": 0,
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
