"""Shared builders of tests/test_api_catalog.py, used by other test modules (#423)."""

from tests import timeline
from tests.timeline import iso

BASE = "/tmf-api/productCatalogManagement/v5"

SPEC = f"{BASE}/productSpecification"

OFFERING = f"{BASE}/productOffering"

PRICE = f"{BASE}/productOfferingPrice"

START = iso(timeline.START)

NIL = "00000000-0000-0000-0000-000000000000"


def make_spec(api, name="Fiber") -> dict:
    body = {"@type": "ProductSpecification", "name": name, "lifecycleStatus": "Active"}
    response = api.post(SPEC, body)
    assert response.status_code == 201, response.text
    return response.json()


def price_body(**over) -> dict:
    return {
        "@type": "ProductOfferingPrice",
        "name": "Monthly",
        "lifecycleStatus": "Active",
        "priceType": "recurring",
        "recurringChargePeriodType": "monthly",
        "price": {"unit": "USD", "value": 500},
        "validFor": {"startDateTime": START},
        **over,
    }


def make_price(api, **over) -> dict:
    response = api.post(PRICE, price_body(**over))
    assert response.status_code == 201, response.text
    return response.json()


def offering_body(spec: dict, **over) -> dict:
    return {
        "@type": "ProductOffering",
        "name": "Fiber 100",
        "lifecycleStatus": "Active",
        "validFor": {"startDateTime": START},
        "productSpecification": {
            "id": spec["id"],
            "href": spec["href"],
            "name": spec["name"],
            "@type": "ProductSpecificationRef",
            "@referredType": "ProductSpecification",
        },
        **over,
    }


def make_offering(api, spec=None, **over) -> dict:
    response = api.post(OFFERING, offering_body(spec or make_spec(api), **over))
    assert response.status_code == 201, response.text
    return response.json()


def price_refs(*prices: dict) -> list[dict]:
    return [{"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in prices]
