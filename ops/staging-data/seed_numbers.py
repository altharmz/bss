"""D4: the numbers 12025550100-12025550119 (existing ones are skipped), the specification
"Mobile Voice & Data (numbered)" that requires a number, and the offerings "Mobile S"
`MOB-NS` and "Mobile L" `MOB-NL` on it, priced as the current MOB-S and MOB-L.

Existing mobile products cannot get numbers (decision B3). A second run finds nothing to
do.

    BSS_URL=... BSS_USER=... BSS_PASSWORD=... python3 seed_numbers.py [--apply]
"""

from typing import Any
from urllib.parse import quote

from _client import CATALOG, Client, ScriptError, run

NUMBERS = "/tmf-api/resourceInventory/v5/resource"
FIRST, COUNT = 12025550100, 20
SPEC_NAME = "Mobile Voice & Data (numbered)"
# new code, name, the code whose price it copies
OFFERINGS = [("MOB-NS", "Mobile S", "MOB-S"), ("MOB-NL", "Mobile L", "MOB-L")]


def offering_by_code(client: Client, code: str) -> dict[str, Any] | None:
    found = client.all(f"{CATALOG}/productOffering?externalIdentifier.id={code}")
    return found[0] if found else None


def current_price(client: Client, offering: dict[str, Any]) -> dict[str, Any]:
    for ref in offering.get("productOfferingPrice") or []:
        price = client.get(f"{CATALOG}/productOfferingPrice/{ref['id']}")
        if price.get("priceType") == "recurring" and price.get("lifecycleStatus") == "Active":
            return price
    raise ScriptError(f"offering {offering['name']!r} has no active recurring price to copy")


def work(client: Client) -> None:
    present = {n["value"] for n in client.all(NUMBERS)}
    for value in (str(FIRST + i) for i in range(COUNT)):
        if value not in present:
            client.post(
                NUMBERS,
                {
                    "@type": "LogicalResource",
                    "name": f"MSISDN {value}",
                    "value": value,
                    "administrativeState": "unlocked",
                    "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                },
                f"create number {value}",
            )

    spec = next(iter(client.all(f"{CATALOG}/productSpecification?name={quote(SPEC_NAME)}")), None)
    spec_id = spec["id"] if spec else None
    if spec is None:
        spec = client.post(
            f"{CATALOG}/productSpecification",
            {
                "@type": "ProductSpecification",
                "name": SPEC_NAME,
                "lifecycleStatus": "Active",
                "resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}],
            },
            f"create specification {SPEC_NAME!r} (requires a number)",
        )
        spec_id = spec["id"] if spec else "<new specification>"

    for code, name, source_code in OFFERINGS:
        if offering_by_code(client, code) is not None:
            continue
        source = offering_by_code(client, source_code)
        if source is None:
            raise ScriptError(f"offering {source_code} not found: nothing to copy the price from")
        price = current_price(client, source)
        created = client.post(
            f"{CATALOG}/productOfferingPrice",
            {
                "@type": "ProductOfferingPrice",
                "name": f"{name} monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": price.get("recurringChargePeriodType", "monthly"),
                "price": price["price"],
            },
            f"create price {name!r} monthly: {price['price']['value']} {price['price']['unit']}",
        )
        client.post(
            f"{CATALOG}/productOffering",
            {
                "@type": "ProductOffering",
                "name": name,
                "lifecycleStatus": "Active",
                "productSpecification": {"id": spec_id, "@type": "ProductSpecificationRef"},
                "productOfferingPrice": [
                    {
                        "id": created["id"] if created else "<new price>",
                        "@type": "ProductOfferingPriceRef",
                    }
                ],
                "externalIdentifier": [
                    {
                        "id": code,
                        "externalIdentifierType": "offeringCode",
                        "@type": "ExternalIdentifier",
                    }
                ],
            },
            f"create offering {name!r} {code} on {SPEC_NAME!r}",
        )


if __name__ == "__main__":
    raise SystemExit(run(__doc__.split("\n\n")[0], work))
