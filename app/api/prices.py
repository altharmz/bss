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


def unit_text(quantity: dict[str, Any]) -> str:
    """TMF620's unitOfMeasure is a Quantity; TMF622/637 state it as text ("Could be
    minutes, GB..."): its units, after the amount unless that is 1."""
    units = quantity.get("units", "")
    amount = quantity.get("amount", 1)
    return units if amount == 1 else f"{amount} {units}".strip()


def monthly_price(
    amount: Decimal,
    currency: str,
    *,
    resource_type: str,
    price_id: Any = None,
    name: str = "Monthly subscription",
    tax: Decimal = Decimal("0.00"),
    tax_rate: Decimal | None = Decimal(0),
    unit_of_measure: dict[str, Any] | None = None,
    billing_account: dict[str, Any] | None = None,
    price_name: str | None = None,
) -> dict[str, Any]:
    """`tax`: the tax on `amount`; `tax_rate`: Price.taxRate in percent (None: omitted -
    a total over items taxed at different rates has no one rate). `price_id`: the
    catalog price it comes from (named `price_name`, default `name`). `unit_of_measure`: the
    catalog price's, if it has one. `priceAlteration` is always empty: billing applies
    no alteration (an alteration price is a record, docs/decisions/catalog.md)."""
    body: dict[str, Any] = {
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
        "priceAlteration": [],
        "@type": resource_type,
    }
    if unit_of_measure is not None:
        body["unitOfMeasure"] = unit_text(unit_of_measure)
    if billing_account is not None:
        body["billingAccount"] = billing_account
    if price_id is not None:
        body["productOfferingPrice"] = ref(
            "ProductOfferingPrice",
            f"{CATALOG_BASE}/productOfferingPrice/{price_id}",
            price_id,
            price_name or name,
        )
    return body
