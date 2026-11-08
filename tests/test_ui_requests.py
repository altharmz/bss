"""The write requests the admin UI sends (frontend/src/api/bss.ts), replayed verbatim
against the real API and its contract checks. The UI's component tests pin these bodies
on the frontend side; this pins that the backend accepts them and stores what the UI
means. If the UI changes a body, change it here too.

Context: business date 2026-10-01 in Europe/Moscow (+03:00), the api fixture's clock.
"""

import base64
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.worker import db as worker_db
from app.worker.queue import process_requests
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import CATALOG, ORDER
from tests.test_api_transfer import pair
from tests.test_prepaid_command_api import failed
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_renewal_producer import tick
from tests.test_worker_process import DEC1, DEC3, at, scenario, set_currency
from tests.timeline import TZ

OFFSET = "+03:00"
RUNS = "/internal/v1/billingRun"


def test_ui_lifecycle_settings(api):
    """Exact partial PUT pinned by BillingSettings.test.tsx, including zero and max."""
    path = "/internal/v1/lifecycle/settings"
    before = api.get(path).json()
    response = api.put(path, {"graceDays": 0, "suspensionDays": 3650})
    assert response.status_code == 200
    saved = api.get(path).json()
    assert saved["graceDays"] == 0
    assert saved["suspensionDays"] == 3650
    assert saved["numberAgingDays"] == before["numberAgingDays"]
    assert saved["paygZeroBalanceDays"] == before["paygZeroBalanceDays"]
    assert saved["updatedAt"] is not None


def test_ui_number_inventory_and_orders(api):
    """Exact inventory/add/modify bodies pinned in Numbers.test.tsx."""
    url = "/tmf-api/resourceInventory/v5/resource"
    numbers = []
    for value in ("12025550123", "12025550124"):
        created = api.post(
            url,
            {
                "@type": "LogicalResource",
                "name": f"MSISDN {value}",
                "value": value,
                "administrativeState": "unlocked",
                "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
            },
        )
        assert created.status_code == 201, created.text
        numbers.append(created.json())
    first, second = numbers
    assert api.patch(first["href"], {"administrativeState": "locked"}).status_code == 200
    assert api.patch(first["href"], {"administrativeState": "unlocked"}).status_code == 200
    offering = apidata.sellable_offering(api)
    assert (
        api.patch(
            offering["productSpecification"]["href"],
            {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
        ).status_code
        == 200
    )
    account = apidata.account(api)

    def product_ref(number):
        return {
            "@type": "ProductRefOrValue",
            "realizingResource": [
                {"id": number["id"], "@type": "ResourceRef", "@referredType": "LogicalResource"}
            ],
        }

    order = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": "2026-10-02T12:00:00+03:00",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                    "product": product_ref(first),
                }
            ],
        },
    )
    assert order.status_code == 201, order.text
    assert order.json()["state"] == "completed", order.text
    product = apidata.product_of(api, order.json())
    api.clock.set(datetime.fromisoformat("2026-10-02T09:00:00+00:00"))
    changed = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "modify",
                    "product": {**product_ref(second), "id": product["id"]},
                }
            ],
        },
    )
    assert changed.status_code == 201, changed.text
    assert changed.json()["state"] == "completed", changed.text
    lookup = api.get(f"{apidata.PRODUCT}?realizingResource.id={second['id']}").json()
    assert [p["id"] for p in lookup] == [product["id"]]


def test_ui_imsi_inventory_orders_and_sim_change(api):
    """Exact IMSI bodies pinned in Numbers.test.tsx: create with ICCID, add with the
    MSISDN and IMSI together, SIM change as the complete resulting set."""
    url = "/tmf-api/resourceInventory/v5/resource"
    ref = {"@type": "ResourceRef", "@referredType": "LogicalResource"}

    def resource(kind, value, extra=None):
        body = {
            "@type": "LogicalResource",
            "name": f"{'IMSI' if kind == 'imsi' else 'MSISDN'} {value}",
            "value": value,
            "administrativeState": "unlocked",
            "resourceSpecification": {"id": kind, "@type": "ResourceSpecificationRef"},
            **(extra or {}),
        }
        created = api.post(url, body)
        assert created.status_code == 201, created.text
        return created.json()

    iccid = {
        "resourceCharacteristic": [
            {
                "@type": "StringCharacteristic",
                "name": "iccid",
                "valueType": "string",
                "value": "8900000000000000001",
            }
        ]
    }
    msisdn = resource("msisdn", "12025550123")
    old = resource("imsi", "001010123456780", iccid)
    new = resource("imsi", "001010123456781")
    offering = apidata.sellable_offering(api)
    assert (
        api.patch(
            offering["productSpecification"]["href"],
            {
                "resourceSpecification": [
                    {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                    {"id": "imsi", "@type": "ResourceSpecificationRef"},
                ]
            },
        ).status_code
        == 200
    )
    account = apidata.account(api)

    def post(item, **extra):
        response = api.post(
            ORDER,
            {
                "@type": "ProductOrder",
                "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
                **extra,
                "productOrderItem": [{"@type": "ProductOrderItem", "id": "1", **item}],
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["state"] == "completed", response.text
        return response.json()

    added = post(
        {
            "action": "add",
            "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
            "product": {
                "@type": "ProductRefOrValue",
                "realizingResource": [{"id": msisdn["id"], **ref}, {"id": old["id"], **ref}],
            },
        },
        requestedStartDate="2026-10-02T12:00:00+03:00",
    )
    product = apidata.product_of(api, added)
    api.clock.set(datetime.fromisoformat("2026-10-02T09:00:00+00:00"))
    post(
        {
            "action": "modify",
            "product": {
                "@type": "ProductRefOrValue",
                "id": product["id"],
                "realizingResource": [{"id": msisdn["id"], **ref}, {"id": new["id"], **ref}],
            },
        }
    )
    lookup = api.get(f"{apidata.PRODUCT}?realizingResource.id={new['id']}").json()
    assert [p["id"] for p in lookup] == [product["id"]]
    assert api.get(old["href"]).json()["administrativeState"] == "locked"


def test_ui_user_management_requests(api):
    """Same bodies as Users.test.tsx and session.test.tsx, through real cookies."""
    from fastapi.testclient import TestClient

    from app.main import app

    session = "/internal/v1/session"
    users = "/internal/v1/users"
    with TestClient(app, headers={"X-Bss-Request": "1"}) as client:
        assert (
            client.post(
                session, json={"username": "i.petrova", "password": "test-password-123"}
            ).status_code
            == 200
        )
        created = client.post(
            users, json={"username": "bob", "role": "admin", "password": "initial-password"}
        )
        assert created.status_code == 201
        assert created.json()["mustChangePassword"] is True
        assert (
            client.post(
                users, json={"username": "alice", "role": "user", "password": "initial-password"}
            ).status_code
            == 201
        )
        mcp_user = client.post(
            users, json={"username": "bob-mcp", "role": "mcp", "password": "initial-password"}
        )
        assert mcp_user.status_code == 201
        assert mcp_user.json()["mustChangePassword"] is False
        assert (
            client.post(
                users, json={"username": "carol", "role": "user", "password": "initial-password"}
            ).status_code
            == 201
        )
        to_mcp = client.patch(
            users + "/carol",
            json={"role": "mcp", "password": "replacement-password"},
            headers={"Content-Type": "application/json"},
        )
        assert to_mcp.status_code == 200
        assert to_mcp.json()["role"] == "mcp"
        changed = client.patch(
            users + "/alice",
            json={"role": "admin", "disabled": True},
            headers={"Content-Type": "application/json"},
        )
        assert changed.status_code == 200
        assert changed.json()["role"] == "admin"
        assert changed.json()["disabled"] is True
        assert (
            client.patch(
                users + "/alice",
                json={"password": "replacement-password"},
                headers={"Content-Type": "application/json"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                session, json={"username": "bob", "password": "initial-password"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                session + "/password",
                json={"currentPassword": "initial-password", "newPassword": "replacement-password"},
            ).status_code
            == 204
        )
        assert client.get(session).status_code == 401
        assert (
            client.post(
                session, json={"username": "bob", "password": "replacement-password"}
            ).status_code
            == 200
        )
        assert client.get(session).json()["mustChangePassword"] is False
        assert client.delete(session).status_code == 204
        assert client.get(session).status_code == 401


@pytest.fixture
def worker(migrated_db_url):
    conn = worker_db.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


def spec(api) -> dict:
    return api.post(
        f"{CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Mobile Voice & Data",
            "lifecycleStatus": "Active",
        },
    ).json()


def create_offering(
    api, code: str = "MOB-XL", unit: str = "monthly", length: int = 1, attributes: bool = False
) -> dict:
    """createOffering(): the price (amount as a raw number token), then the offering."""
    valid_for = (
        '{"startDateTime":"2026-10-02T00:00:00+03:00","endDateTime":"2027-01-01T00:00:00+03:00"}'
    )
    multiplier = (
        f'"recurringChargePeriodLength":{length},' if (unit, length) != ("monthly", 1) else ""
    )
    price = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        f'{{"@type":"ProductOfferingPrice","name":"Mobile XL {unit}","lifecycleStatus":"Active",'
        f'"priceType":"recurring","recurringChargePeriodType":"{unit}",{multiplier}'
        f'"price":{{"unit":"USD","value":1990.50}},"validFor":{valid_for}}}',
    )
    assert price.status_code == 201, price.text
    optional = {}
    if attributes:
        categories = [
            api.post(f"{CATALOG}/category", {"@type": "Category", "name": name}).json()
            for name in ("Mobile", "Promo")
        ]
        optional = {
            "description": "Unlimited data",
            "isSellable": False,
            "category": [{"id": category["id"], "@type": "CategoryRef"} for category in categories],
        }
    offering = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Mobile XL",
            **optional,
            "lifecycleStatus": "In Design",
            "validFor": {
                "startDateTime": "2026-10-02T00:00:00+03:00",
                "endDateTime": "2027-01-01T00:00:00+03:00",
            },
            "productSpecification": {"id": spec(api)["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [
                {"id": price.json()["id"], "@type": "ProductOfferingPriceRef"}
            ],
            "externalIdentifier": [
                {
                    "id": code,
                    "externalIdentifierType": "offeringCode",
                    "@type": "ExternalIdentifier",
                }
            ],
        },
    )
    assert offering.status_code == 201, offering.text
    return offering.json()


@pytest.mark.parametrize(
    "unit,length", [("monthly", 1), ("daily", 3), ("weekly", 2), ("monthly", 3), ("yearly", 1)]
)
def test_new_offering_is_stored_as_the_ui_means_it(api, unit, length):
    offering = create_offering(api, unit=unit, length=length, attributes=True)
    assert offering["lifecycleStatus"] == "In Design"
    assert offering["description"] == "Unlimited data"
    assert offering["isSellable"] is False
    assert len(offering["category"]) == 2
    assert all(ref["@type"] == "CategoryRef" for ref in offering["category"])
    assert offering["externalIdentifier"][0]["id"] == "MOB-XL"
    # "valid to 2026-12-31" (inclusive) is stored as the exclusive midnight of Jan 1
    assert offering["validFor"] == {
        "startDateTime": "2026-10-01T21:00:00Z",
        "endDateTime": "2026-12-31T21:00:00Z",
    }
    price = decimals(api.get(offering["productOfferingPrice"][0]["href"]))
    assert price["price"] == {"unit": "USD", "value": Decimal("1990.50")}
    assert (price["recurringChargePeriodType"], price["recurringChargePeriodLength"]) == (
        unit,
        length,
    )


def test_add_price_preserves_the_complete_price_reference_list(api):
    """addPrice(): exact POST bodies and old-plus-new offering references from Catalog.test.tsx."""
    offering = create_offering(api)
    original = offering["productOfferingPrice"][0]
    assert api.delete(original["href"]).status_code == 204

    one_time = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        '{"@type":"ProductOfferingPrice","name":"Mobile XL oneTime","lifecycleStatus":"Active",'
        '"priceType":"oneTime","price":{"unit":"USD","value":125.50},'
        '"validFor":{"startDateTime":"2026-10-02T00:00:00+03:00"}}',
    )
    assert one_time.status_code == 201, one_time.text
    one_time_ref = {"id": one_time.json()["id"], "@type": "ProductOfferingPriceRef"}
    attached = api.patch(offering["href"], {"productOfferingPrice": [one_time_ref]})
    assert attached.status_code == 200, attached.text

    recurring = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        '{"@type":"ProductOfferingPrice","name":"Mobile XL monthly","lifecycleStatus":"Active",'
        '"priceType":"recurring","recurringChargePeriodType":"monthly",'
        '"price":{"unit":"USD","value":130.00},'
        '"validFor":{"startDateTime":"2026-10-02T00:00:00+03:00"}}',
    )
    assert recurring.status_code == 201, recurring.text
    recurring_ref = {"id": recurring.json()["id"], "@type": "ProductOfferingPriceRef"}
    attached = api.patch(offering["href"], {"productOfferingPrice": [one_time_ref, recurring_ref]})
    assert attached.status_code == 200, attached.text
    assert [ref["id"] for ref in attached.json()["productOfferingPrice"]] == [
        one_time_ref["id"],
        recurring_ref["id"],
    ]
    assert "recurringChargePeriodType" not in decimals(api.get(one_time.json()["href"]))
    monthly = decimals(api.get(recurring.json()["href"]))
    assert monthly["price"] == {"unit": "USD", "value": Decimal("130.00")}
    assert monthly["recurringChargePeriodType"] == "monthly"


def test_usage_price_writes_as_the_ui_sends_them(api):
    """Catalog.test.tsx: a usage price POST, complete reference PATCH and editable fields."""
    sms = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        '{"@type":"ProductOfferingPrice","name":"Metered sms usage","lifecycleStatus":"Active",'
        '"priceType":"usage","unitOfMeasure":{"amount":1,"units":"message"},'
        '"price":{"unit":"USD","value":0.10},'
        '"validFor":{"startDateTime":"2026-10-02T00:00:00+03:00"}}',
    )
    assert sms.status_code == 201, sms.text
    existing = sms.json()
    offering = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Metered",
            "lifecycleStatus": "Active",
            "productSpecification": {"id": spec(api)["id"], "@type": "ProductSpecificationRef"},
            "productOfferingCharacteristic": [
                {
                    "@type": "CharacteristicSpecification",
                    "name": "offeringKind",
                    "valueType": "string",
                    "characteristicValueSpecification": [
                        {
                            "@type": "StringCharacteristicValueSpecification",
                            "valueType": "string",
                            "isDefault": True,
                            "value": "payAsYouGo",
                        }
                    ],
                }
            ],
            "productOfferingPrice": [{"id": existing["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )
    assert offering.status_code == 201, offering.text
    created = api.post_raw(
        f"{CATALOG}/productOfferingPrice",
        '{"@type":"ProductOfferingPrice","name":"Metered voice usage","lifecycleStatus":"Active",'
        '"priceType":"usage","unitOfMeasure":{"amount":10,"units":"minute"},'
        '"price":{"unit":"USD","value":0.25},'
        '"validFor":{"startDateTime":"2026-10-02T00:00:00+03:00"}}',
    )
    assert created.status_code == 201, created.text
    refs = [
        {"id": existing["id"], "@type": "ProductOfferingPriceRef"},
        {"id": created.json()["id"], "@type": "ProductOfferingPriceRef"},
    ]
    attached = api.patch(offering.json()["href"], {"productOfferingPrice": refs})
    assert attached.status_code == 200, attached.text
    assert [ref["id"] for ref in attached.json()["productOfferingPrice"]] == [
        existing["id"],
        created.json()["id"],
    ]

    changed = api.patch_raw(
        created.json()["href"],
        '{"name":"Metered voice 2","description":"New rounding description",'
        '"price":{"unit":"USD","value":0.50}}',
    )
    assert changed.status_code == 200, changed.text
    stored = decimals(api.get(created.json()["href"]))
    assert stored["name"] == "Metered voice 2"
    assert stored["description"] == "New rounding description"
    assert stored["price"] == {"unit": "USD", "value": Decimal("0.50")}
    assert stored["priceType"] == "usage"
    assert stored["unitOfMeasure"] == {"amount": 10, "units": "minute"}


def test_specification_writes_as_the_ui_sends_them(api):
    """createSpecification(), patchSpecification() and deleteSpecification(): bodies pinned
    in Catalog.test.tsx."""
    url = f"{CATALOG}/productSpecification"
    created = api.post(
        url,
        {
            "@type": "ProductSpecification",
            "name": "Roaming",
            "lifecycleStatus": "In Design",
            "brand": "Acme",
            "version": "1.0",
            "productNumber": "PN-9",
        },
    )
    assert created.status_code == 201, created.text
    spec_url = created.json()["href"]
    assert created.json()["brand"] == "Acme" and created.json()["productNumber"] == "PN-9"
    # only the changed fields; an emptied optional text is null (merge patch removes it)
    changed = api.patch(spec_url, {"name": "Roaming 2", "brand": None, "lifecycleStatus": "Active"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["name"] == "Roaming 2" and body["lifecycleStatus"] == "Active"
    assert "brand" not in body and body["version"] == "1.0"
    # the forward step only; the server's 409 is shown by the dialog
    assert api.patch(spec_url, {"lifecycleStatus": "In Design"}).status_code == 409
    # a used specification is a 409; an unused one is deleted with no body
    offering = create_offering(api)
    used = offering["productSpecification"]["href"]
    assert api.delete(used).status_code == 409
    assert api.delete(spec_url).status_code == 204
    assert api.get(spec_url).status_code == 404


@pytest.mark.parametrize(
    "value_type,values", [("string", ["red", "blue"]), ("integer", [1, 2]), ("boolean", [])]
)
def test_numbered_specification_documents_as_the_ui_sends_them(api, value_type, values):
    """G4b editor create/patch documents, pinned in Catalog.test.tsx."""
    resource = [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]
    characteristic = {
        "@type": "CharacteristicSpecification",
        "name": "Option",
        "description": "Choices",
        "configurable": True,
        "valueType": value_type,
        "characteristicValueSpecification": [
            {
                "@type": f"{value_type.capitalize()}CharacteristicValueSpecification",
                "valueType": value_type,
                "isDefault": index == 0,
                "value": value,
            }
            for index, value in enumerate(values)
        ],
    }
    created = api.post(
        f"{CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Numbered",
            "lifecycleStatus": "In Design",
            "resourceSpecification": resource,
            "productSpecCharacteristic": [characteristic],
        },
    )
    assert created.status_code == 201, created.text
    spec = created.json()
    assert spec["resourceSpecification"] == resource
    assert spec["productSpecCharacteristic"] == [characteristic]
    plain = api.post(
        f"{CATALOG}/productSpecification",
        {"name": "Plain", "@type": "ProductSpecification", "lifecycleStatus": "In Design"},
    ).json()
    enabled = {
        "@type": "CharacteristicSpecification",
        "name": "Enabled",
        "description": "",
        "configurable": False,
        "valueType": "integer",
        "characteristicValueSpecification": [
            {
                "@type": "IntegerCharacteristicValueSpecification",
                "valueType": "integer",
                "isDefault": index == 0,
                "value": value,
            }
            for index, value in enumerate([1, 2])
        ],
    }
    changed = api.patch(
        plain["href"], {"resourceSpecification": resource, "productSpecCharacteristic": [enabled]}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["productSpecCharacteristic"] == [enabled]
    removed = api.patch(
        plain["href"], {"resourceSpecification": [], "productSpecCharacteristic": []}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["resourceSpecification"] == []
    assert removed.json()["productSpecCharacteristic"] == []


def test_offering_and_price_edits_as_the_ui_sends_them(api):
    """offeringChanges(), patchOffering(), patchPrice(), endPrice() and the deletes: bodies
    pinned in Catalog.test.tsx."""
    offering = create_offering(api)
    category = api.post(f"{CATALOG}/category", {"name": "Promo"}).json()
    other_spec = api.post(
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Roaming", "lifecycleStatus": "Active"},
    ).json()
    crm = {"id": "CRM-7", "externalIdentifierType": "crm", "@type": "ExternalIdentifier"}
    # another identifier next to the code, as an API client may have stored
    assert (
        api.patch(
            offering["href"],
            {
                "externalIdentifier": [
                    {**offering["externalIdentifier"][0]},
                    crm,
                ]
            },
        ).status_code
        == 200
    )
    patched = api.patch(
        offering["href"],
        {
            "name": "Mobile XL 2",
            "description": "Big",
            "isSellable": False,
            # the start as loaded, the end the midnight after the last valid day
            "validFor": {
                "startDateTime": offering["validFor"]["startDateTime"],
                "endDateTime": "2027-02-01T00:00:00+03:00",
            },
            "externalIdentifier": [
                {**offering["externalIdentifier"][0], "id": "MOB-XL2"},
                crm,
            ],
            "productSpecification": {"id": other_spec["id"], "@type": "ProductSpecificationRef"},
            "category": [{"id": category["id"], "@type": "CategoryRef"}],
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "Mobile XL 2" and body["isSellable"] is False
    assert [i["id"] for i in body["externalIdentifier"]] == ["MOB-XL2", "CRM-7"]
    assert [c["id"] for c in body["category"]] == [category["id"]]
    assert body["productSpecification"]["id"] == other_spec["id"]
    # an emptied description is null (merge patch removes it)
    assert "description" not in api.patch(offering["href"], {"description": None}).json()

    price_url = body["productOfferingPrice"][0]["href"]
    # the amount is a raw number token, next to the other changed fields
    renamed = api.patch_raw(
        price_url, '{"name":"Mobile XL 2 monthly","price":{"unit":"USD","value":2100.50}}'
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["price"] == {"unit": "USD", "value": 2100.5}
    price = api.get(price_url).json()
    ended = api.patch(
        price_url,
        {
            "validFor": {
                "startDateTime": price["validFor"]["startDateTime"],
                "endDateTime": "2026-12-01T00:00:00+03:00",
            }
        },
    )
    assert ended.status_code == 200, ended.text
    # an offering with a price is not deleted (the UI shows the 409)
    assert api.delete(offering["href"]).status_code == 409
    fresh = create_offering(api, code="MOB-NEW")
    fresh_price = fresh["productOfferingPrice"][0]["href"]
    assert api.delete(fresh_price).status_code == 204
    assert api.delete(fresh["href"]).status_code == 204
    assert api.get(fresh["href"]).status_code == 404


def test_category_and_catalog_writes_as_the_ui_sends_them(api):
    """createCategory(), categoryChanges(), createCatalog(), catalogChanges() and the deletes:
    bodies pinned in Catalog.test.tsx."""
    offering = create_offering(api)
    other = create_offering(api, code="MOB-OTHER")
    home = api.post(f"{CATALOG}/category", {"@type": "Category", "name": "Home"}).json()
    created = api.post(
        f"{CATALOG}/category",
        {
            "@type": "Category",
            "name": "Roaming",
            "description": "Abroad",
            "lifecycleStatus": "In Design",
            "validFor": {
                "startDateTime": "2026-11-01T00:00:00+03:00",
                "endDateTime": "2027-01-01T00:00:00+03:00",
            },
            "parent": {"id": home["id"], "@type": "CategoryRef"},
            "productOffering": [{"id": offering["id"], "@type": "ProductOfferingRef"}],
        },
    )
    assert created.status_code == 201, created.text
    category = created.json()
    assert category["parent"]["id"] == home["id"] and category["isRoot"] is False
    # an end without a start is refused (the UI keeps Create disabled)
    assert (
        api.post(
            f"{CATALOG}/category",
            {
                "@type": "Category",
                "name": "X",
                "validFor": {"endDateTime": "2027-01-01T00:00:00+03:00"},
            },
        ).status_code
        == 422
    )
    assert [o["id"] for o in category["productOffering"]] == [offering["id"]]
    # the membership is replaced as a whole; the parent goes with null; only changed fields
    patched = api.patch(
        category["href"],
        {
            "name": "Roaming 2",
            "lifecycleStatus": "Active",
            "parent": None,
            "productOffering": [{"id": other["id"], "@type": "ProductOfferingRef"}],
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["isRoot"] is True and "parent" not in body
    assert [o["id"] for o in body["productOffering"]] == [other["id"]]
    # a cycle (a parent below the category) is refused; the UI leaves it out of the select
    child = api.post(
        f"{CATALOG}/category",
        {
            "@type": "Category",
            "name": "Sub",
            "parent": {"id": category["id"], "@type": "CategoryRef"},
        },
    ).json()
    cycle = api.patch(category["href"], {"parent": {"id": child["id"], "@type": "CategoryRef"}})
    assert cycle.status_code == 409
    # a category with subcategories or offerings is not deleted (the UI hides Delete)
    assert api.delete(category["href"]).status_code == 409

    catalog = api.post(
        f"{CATALOG}/productCatalog",
        {
            "@type": "ProductCatalog",
            "name": "Wholesale",
            "lifecycleStatus": "In Design",
            "validFor": {"startDateTime": "2026-11-01T00:00:00+03:00"},
            "catalogType": "b2b",
            "category": [{"id": home["id"], "@type": "CategoryRef"}],
        },
    )
    assert catalog.status_code == 201, catalog.text
    catalog = catalog.json()
    assert catalog["catalogType"] == "b2b" and [c["id"] for c in catalog["category"]] == [
        home["id"]
    ]
    changed = api.patch(
        catalog["href"],
        {"catalogType": None, "category": [{"id": category["id"], "@type": "CategoryRef"}]},
    )
    assert changed.status_code == 200, changed.text
    assert "catalogType" not in changed.json()
    assert [c["id"] for c in changed.json()["category"]] == [category["id"]]
    # a catalog takes root categories only (the UI lists only those)
    assert (
        api.patch(
            catalog["href"], {"category": [{"id": child["id"], "@type": "CategoryRef"}]}
        ).status_code
        == 409
    )
    assert api.delete(catalog["href"]).status_code == 204
    assert api.get(catalog["href"]).status_code == 404
    assert api.delete(child["href"]).status_code == 204
    assert api.delete(home["href"]).status_code == 204


def test_catalog_jobs_as_the_ui_sends_them(api):
    """exportBody(), importBody(), toDataUrl() and deleteJob(): bodies pinned in
    Catalog.test.tsx. An export, then its import, is a no-op success."""
    create_offering(api)
    home = api.post(f"{CATALOG}/category", {"@type": "Category", "name": "Home"}).json()
    k = api.post(
        f"{CATALOG}/productCatalog",
        {
            "@type": "ProductCatalog",
            "name": "Retail",
            "category": [{"id": home["id"], "@type": "CategoryRef"}],
        },
    ).json()
    prefix = "data:application/json;base64,"
    for query in (None, f"productCatalog.id={k['id']}"):
        body = {"@type": "ExportJob", "path": CATALOG, "contentType": "application/json"}
        if query:
            body["query"] = query
        exported = api.post(f"{CATALOG}/exportJob", body)
        assert exported.status_code == 201, exported.text
        job = exported.json()
        assert job["status"] == "Succeeded" and job["url"].startswith(prefix)
        # the UI decodes the url into the downloaded file and pastes it back as text
        text = base64.b64decode(job["url"][len(prefix) :]).decode()
        replay = api.post(
            f"{CATALOG}/importJob",
            {
                "@type": "ImportJob",
                "path": CATALOG,
                "contentType": "application/json",
                "url": prefix + base64.b64encode(text.encode()).decode(),
            },
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["status"] == "Succeeded", replay.json()
        assert api.delete(f"{CATALOG}/exportJob/{job['id']}").status_code == 204
        assert api.delete(f"{CATALOG}/importJob/{replay.json()['id']}").status_code == 204
    # a document the server refuses is a stored Failed job with its errorLog
    bad = base64.b64encode(b'{"@type":"BssCatalogDocument","version":2}').decode()
    failed = api.post(
        f"{CATALOG}/importJob",
        {
            "@type": "ImportJob",
            "path": CATALOG,
            "contentType": "application/json",
            "url": prefix + bad,
        },
    )
    assert failed.status_code == 201
    assert failed.json()["status"] == "Failed" and "version" in failed.json()["errorLog"]
    listed = api.get(f"{CATALOG}/importJob?offset=0&limit=100")
    assert [j["id"] for j in listed.json()] == [failed.json()["id"]]
    assert api.delete(f"{CATALOG}/importJob/{failed.json()['id']}").status_code == 204


def test_lifecycle_steps_are_merge_patches(api):
    offering = create_offering(api)
    for status in ("Active", "Retired"):  # NEXT_LIFECYCLE
        response = api.patch(offering["href"], {"lifecycleStatus": status})
        assert response.status_code == 200, response.text
        assert response.json()["lifecycleStatus"] == status


def test_add_and_delete_orders_as_the_ui_sends_them(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))  # prices are valid from Oct 2
    # orderAdd(): requestedStartDate is noon of the chosen day in BILLING_TZ
    added = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": f"2026-10-06T12:00:00{OFFSET}",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                }
            ],
        },
    )
    assert added.status_code == 201 and added.json()["state"] == "completed", added.text
    product = apidata.product_of(api, added.json())
    started = product["startDate"]
    # midnight of 2026-10-06 in Moscow: the day the operator chose, not a day off
    assert started == "2026-10-05T21:00:00Z"

    # orderDelete(): no requestedStartDate = today; a product not started yet is cancelled
    deleted = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "delete",
                    "product": {"id": product["id"], "@type": "ProductRef"},
                }
            ],
        },
    )
    assert deleted.status_code == 201 and deleted.json()["state"] == "completed", deleted.text
    assert api.get(product["href"]).json()["status"] == "cancelled"
    assert TZ.key == "Europe/Moscow"  # the offsets above assume it


def test_undo_deactivation_as_the_ui_sends_it(api):
    """undoDeactivation(): the product's termination order from TMF637, then a
    cancelProductOrder with only productOrder."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    apidata.order(
        api,
        account,
        apidata.delete_item(product["id"]),
        requestedStartDate=f"2026-10-20T12:00:00{OFFSET}",
    )
    ending = api.get(product["href"]).json()
    assert ending["status"] == "pendingTerminate"
    (termination,) = [i for i in ending["productOrderItem"] if i["role"] == "termination"]
    cancelled = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": termination["orderId"], "@type": "ProductOrderRef"},
        },
    )
    assert cancelled.status_code == 201, cancelled.text
    assert api.get(product["href"]).json()["status"] == "active"


def test_price_change_as_the_ui_sends_it(api):
    """changePrice(): the amount as a raw number token, the date as a business date."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    assert apidata.order(api, account, apidata.add_item(offering))["state"] == "completed"
    response = api.post_raw(
        f"/internal/v1/productOffering/{offering['id']}/priceChange",
        '{"amount":1390.00,"currency":"USD","validFrom":"2026-11-01"}',
    )
    assert response.status_code == 201, response.text
    assert decimals(response)["newPrice"]["price"] == {"unit": "USD", "value": Decimal("1390.00")}


def started_after(api, queued_at: str) -> list[dict]:
    """The runs the Billing runs screen counts as carrying a queued request out: started at
    or after its requestedAt (frontend/src/screens/BillingRuns.tsx, carriedOut)."""
    since = datetime.fromisoformat(queued_at)
    runs = api.get(f"{RUNS}?limit=14").json()
    return [r for r in runs if datetime.fromisoformat(r["startedAt"]) >= since]


def test_retry_as_the_ui_sends_it(api, worker):
    """retryRun(): a POST without a body or content type. Once the worker has carried it
    out, the retried run reads as started after the request - what the screen waits for."""
    s = scenario(api, worker, broken=True)  # x failed on Dec 1, blocked on Dec 2 and 3
    set_currency(worker, s["fragile"]["id"], "USD")  # the fix
    api.clock.set(at(DEC3, 12))
    dec1 = s["runs"][DEC1]["id"]
    response = api.request("POST", f"{RUNS}/{dec1}/retry")
    assert response.status_code == 202, response.text
    queued_at = response.json()["request"]["requestedAt"]
    assert started_after(api, queued_at) == []  # not carried out yet: the screen polls

    api.clock.set(at(DEC3, 12, 1))  # the worker takes it within a minute
    process_requests(worker, clock=api.clock, tz=TZ)
    runs = started_after(api, queued_at)
    assert sorted(r["runDate"] for r in runs) == ["2026-12-01", "2026-12-02", "2026-12-03"]
    assert {r["status"] for r in runs} == {"completed"}  # none running: the screen stops


def test_catch_up_as_the_ui_sends_it(api, worker):
    """catchUp(): {"upTo": "YYYY-MM-DD"}, a business date. The new runs read as started
    after the request once the worker has carried it out."""
    scenario(api, worker, broken=False)  # runs Dec 1, 2, 3
    api.clock.set(at(DEC3.replace(day=5), 1))  # 01:00 on Dec 5: Dec 5 is due (00:30)
    response = api.post_raw(f"{RUNS}/catchUp", '{"upTo":"2026-12-05"}')
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["dates"] == ["2026-12-04", "2026-12-05"]
    assert started_after(api, body["request"]["requestedAt"]) == []

    api.clock.set(at(DEC3.replace(day=5), 1, 1))
    process_requests(worker, clock=api.clock, tz=TZ)
    runs = started_after(api, body["request"]["requestedAt"])
    assert [(r["runDate"], r["status"], r["trigger"]) for r in runs] == [
        ("2026-12-05", "completed", "catchUp"),
        ("2026-12-04", "completed", "catchUp"),
    ]
    # nothing is due any more: 200, no dates, nothing queued (the screen's "Nothing to catch up")
    again = api.post_raw(f"{RUNS}/catchUp", '{"upTo":"2026-12-05"}')
    assert again.status_code == 200 and again.json() == {"dates": []}


# --- bills (frontend/src/screens/Bills.test.tsx pins the same bodies) ---

PAYMENT = "/tmf-api/payment/v4/payment"
BILL = "/tmf-api/customerBillManagement/v5/customerBill"
CYCLES = "/tmf-api/customerBillManagement/v5/billCycle"
RATES = "/tmf-api/customerBillManagement/v5/appliedCustomerBillingRate"


def test_refund_and_bill_request_as_the_ui_sends_them(api):
    """AccountActions pins the same exact decimal and typed reference bodies."""
    account = apidata.account(api)
    a = account["id"]
    assert api.post(PAYMENT, apidata.payment_body(account, 100)).status_code == 201
    refund = api.post_raw(
        "/tmf-api/payment/v4/refund",
        f'{{"@type":"Refund","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":25.10},'
        '"paymentMethod":{"name":"bankTransfer"},"description":"Returned deposit"}',
    )
    assert refund.status_code == 201, refund.text
    assert decimals(refund)["totalAmount"]["value"] == Decimal("25.10")
    assert refund.json()["description"] == "Returned deposit"
    assert refund.json()["paymentMethod"]["name"] == "bankTransfer"
    assert refund.json()["status"] == "done"
    assert "payment" not in refund.json()
    demands = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
    body = (
        '{"@type":"CustomerBillOnDemand","billingAccount":'
        f'{{"id":"{a}","@referredType":"BillingAccount"}}}}'
    )
    queued = api.post_raw(demands, body)
    assert queued.status_code == 201, queued.text
    assert queued.json()["billingAccount"]["id"] == a
    assert queued.json()["state"] == "inProgress"
    assert api.post_raw(demands, body).status_code == 409
    assert api.get(f"{demands}?billingAccount.id={a}").json() == [queued.json()]


def two_open_bills(api, db_conn) -> tuple[dict, list[dict]]:
    """An account with the 300.00 bills of Dec 1 and Jan 1, both unpaid."""
    from datetime import date

    from tests.test_billing_db import add, bill
    from tests.test_billing_db import at as at_day

    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at_day(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    bill(db_conn, account, date(2027, 1, 1))
    api.clock.set(at_day(date(2027, 1, 2)))
    return account, decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))


def test_payments_as_the_ui_sends_them(api, db_conn):
    """recordPayment(): totalAmount and every item amount as exact number tokens, the
    method as paymentMethod.name; without items the oldest bills are paid first."""
    account, (dec, jan) = two_open_bills(api, db_conn)
    a = account["id"]
    plain = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":350.00},"paymentMethod":{"name":"bankTransfer"}}',
    )
    assert plain.status_code == 201, plain.text
    assert [i["item"]["id"] for i in plain.json()["paymentItem"]] == [dec["id"], jan["id"]]

    lettered = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":100.00},"paymentMethod":{"name":"cash"},'
        f'"paymentItem":[{{"@type":"PaymentItem","item":{{"id":"{jan["id"]}",'
        '"@referredType":"CustomerBill"},"amount":{"unit":"USD","value":100.00}}]}',
    )
    assert lettered.status_code == 201, lettered.text
    bills = decimals(api.get(f"{BILL}?billingAccount.id={a}"))
    assert [(b["state"], b["remainingAmount"]["value"]) for b in bills] == [
        ("settled", Decimal("0.00")),
        ("partiallyPaid", Decimal("150.00")),
    ]


def test_hold_and_release_as_the_ui_sends_them(api, db_conn):
    """setBillState(): a merge patch {"state": ...}; release sends the state the dialog got
    in allowedStates."""
    _, (dec, _) = two_open_bills(api, db_conn)
    held = api.patch(f"{BILL}/{dec['id']}", {"state": "onHold"})
    assert held.status_code == 200 and held.json()["state"] == "onHold"
    allowed = api.get(f"/internal/v1/customerBill/{dec['id']}").json()["allowedStates"]
    assert allowed == ["sent"]
    released = api.patch(f"{BILL}/{dec['id']}", {"state": allowed[0]})
    assert released.status_code == 200 and released.json()["state"] == "sent"


def test_global_bill_cycle_and_rate_reads_as_the_ui_makes_them(api, db_conn):
    """api/bills.ts, pinned in GlobalBills.test.tsx: the bill list by billingAccount.id and
    state (paged by getAll; the billing date is applied client side), the bill's billCycle
    ref and runType, the bill cycle list and GET, and the rates by bill.id with one rate."""
    account, (dec, jan) = two_open_bills(api, db_conn)
    a = account["id"]
    page = "offset=0&limit=100"
    mine = api.get(f"{BILL}?billingAccount.id={a}&state=sent&{page}")
    assert {b["id"] for b in mine.json()} == {dec["id"], jan["id"]}
    assert api.get(f"{BILL}?state=settled&{page}").json() == []
    assert len(api.get(f"{BILL}?{page}").json()) >= 2
    row = mine.json()[0]
    assert row["billingAccount"]["id"] == a and row["billingAccount"]["name"]
    assert row["billDate"] and row["runType"] in ("onCycle", "offCycle")
    assert row["amountDue"]["unit"] == "USD"
    cycle_id = row["billCycle"]["id"]

    cycles = api.get(f"{CYCLES}?{page}")
    assert cycle_id in {c["id"] for c in cycles.json()}
    one = next(c for c in cycles.json() if c["id"] == cycle_id)
    assert one["name"] and one["billingDate"] and one["paymentDueDate"]
    assert api.get(f"{CYCLES}/{cycle_id}").json()["id"] == cycle_id

    rates = api.get(f"{RATES}?bill.id={dec['id']}&{page}")
    assert rates.status_code == 200 and rates.json()
    rate = rates.json()[0]
    assert rate["periodCoverage"]["startDateTime"] and rate["product"]["name"]
    assert rate["taxExcludedAmount"]["unit"] == "USD" and "appliedTax" in rate
    assert api.get(f"{RATES}/{rate['id']}").json()["id"] == rate["id"]


# --- subscribers (frontend/src/screens/Subscribers.test.tsx, SubscriberCard.test.tsx) ---

PARTY = "/tmf-api/partyManagement/v5"
CUSTOMERS = "/tmf-api/customer/v5/customer"
ACCOUNTS = "/tmf-api/accountManagement/v5/billingAccount"


def create_subscriber_as_the_ui_does(api, credit_limit: str | None = "1500.50") -> dict:
    """createSubscriber(): party, customer (Active, engaging it), account."""
    party = api.post_raw(
        f"{PARTY}/individual",
        '{"@type":"Individual","givenName":"Olga","familyName":"Kuznetsova"}',
    )
    assert party.status_code == 201, party.text
    customer = api.post_raw(
        CUSTOMERS,
        '{"@type":"Customer","name":"Olga Kuznetsova","status":"Active",'
        f'"engagedParty":{{"id":"{party.json()["id"]}","@type":"PartyRef",'
        '"@referredType":"Individual"}}',
    )
    assert customer.status_code == 201, customer.text
    limit = (
        "" if credit_limit is None else f',"creditLimit":{{"unit":"USD","value":{credit_limit}}}'
    )
    account = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,'
        '"currency":"USD","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",'
        f'"role":"customer","partyOrPartyRole":{{"id":"{customer.json()["id"]}",'
        '"name":"Olga Kuznetsova","@type":"PartyRoleRef","@referredType":"Customer"}}]'
        f"{limit}}}",
    )
    assert account.status_code == 201, account.text
    return {"party": party.json(), "customer": customer.json(), "account": account.json()}


def test_new_subscriber_as_the_ui_creates_it(api):
    made = create_subscriber_as_the_ui_does(api)
    account = decimals(api.get(made["account"]["href"]))
    assert account["creditLimit"] == {"unit": "USD", "value": Decimal("1500.50")}
    assert (account["billCycleDay"], account["state"]) == (15, "Active")
    assert account["relatedParty"][0]["partyOrPartyRole"]["name"] == "Olga Kuznetsova"
    assert made["customer"]["status"] == "Active"
    assert made["customer"]["engagedParty"]["@referredType"] == "Individual"
    org = api.post_raw(f"{PARTY}/organization", '{"@type":"Organization","name":"Vasilek LLC"}')
    assert org.status_code == 201, org.text


def test_add_billing_account_as_the_ui_sends_it(api):
    """createBillingAccount() (Add billing account on the customer dialog and in the subscriber
    card's More actions, pinned in Parties.test.tsx and SubscriberCard.test.tsx): the account
    step of New subscriber against an existing customer, postpaid with a limit and prepaid."""
    made = create_subscriber_as_the_ui_does(api)
    customer = made["customer"]["id"]
    party = (
        '"relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef","role":"customer",'
        f'"partyOrPartyRole":{{"id":"{customer}","name":"Olga Kuznetsova",'
        '"@type":"PartyRoleRef","@referredType":"Customer"}}]'
    )
    postpaid = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,'
        f'"currency":"USD",{party},"creditLimit":{{"unit":"USD","value":1500.50}}}}',
    )
    assert postpaid.status_code == 201, postpaid.text
    prepaid = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Wallet","billCycleDay":1,"currency":"USD",'
        f'"ratingType":"prepaid",{party}}}',
    )
    assert prepaid.status_code == 201, prepaid.text
    assert prepaid.json()["ratingType"] == "prepaid"
    assert postpaid.json()["relatedParty"][0]["partyOrPartyRole"]["id"] == customer


def test_new_subscriber_compensation_deletes_as_the_ui_does(api):
    """createSubscriber() undoing itself (Subscribers.test.tsx): DELETE the customer, then the
    party, with no body. The customer cannot go while it has an account (409) and the party
    cannot go while it has the customer, which is why a failed compensation stops at the first."""
    made = create_subscriber_as_the_ui_does(api)
    party, customer = made["party"]["id"], made["customer"]["id"]
    assert api.request("DELETE", f"{PARTY}/individual/{party}").status_code == 409
    assert api.request("DELETE", f"{CUSTOMERS}/{customer}").status_code == 409
    assert api.request("DELETE", made["account"]["href"]).status_code == 204
    assert api.request("DELETE", f"{CUSTOMERS}/{customer}").status_code == 204
    assert api.request("DELETE", f"{PARTY}/individual/{party}").status_code == 204


def test_new_prepaid_subscriber_as_the_ui_creates_it(api):
    """createSubscriber() with the prepaid mode: ratingType in the body, no credit limit; the
    card then reads the account's bucket by partyAccount.id (TMF654)."""
    party = api.post_raw(
        f"{PARTY}/individual", '{"@type":"Individual","givenName":"Olga","familyName":"K"}'
    )
    customer = api.post_raw(
        CUSTOMERS,
        '{"@type":"Customer","name":"Olga K","status":"Active",'
        f'"engagedParty":{{"id":"{party.json()["id"]}","@type":"PartyRef",'
        '"@referredType":"Individual"}}',
    )
    assert customer.status_code == 201, customer.text
    account = api.post_raw(
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga K main","billCycleDay":1,"currency":"USD",'
        '"ratingType":"prepaid","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",'
        f'"role":"customer","partyOrPartyRole":{{"id":"{customer.json()["id"]}",'
        '"name":"Olga K","@type":"PartyRoleRef","@referredType":"Customer"}}]}',
    )
    assert account.status_code == 201, account.text
    assert account.json()["ratingType"] == "prepaid"
    buckets = api.get(
        "/tmf-api/prepayBalanceManagement/v4/bucket"
        f"?partyAccount.id={account.json()['id']}&offset=0&limit=100"
    )
    assert buckets.status_code == 200 and len(buckets.json()) == 1
    bucket = decimals(buckets)[0]
    assert (bucket["remainingValue"]["amount"], bucket["reservedValue"]["amount"]) == (0, 0)


def test_prepaid_money_forms_as_the_ui_sends_them(api):
    """balanceActionRequest() (PrepaidMoney.tsx): the bodies pinned in SubscriberCard.test.tsx,
    each under an Idempotency-Key. A retry of the same submission replays; a debit above the
    spendable funds is a stored refusal (201, failed) the form shows as the reason."""
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    account = apidata.account(api, ratingType="prepaid")
    found = api.get(f"{prepay}/bucket?partyAccount.id={account['id']}&offset=0&limit=100")
    bucket = found.json()[0]
    owner = f'"partyAccount":{{"id":"{account["id"]}","@referredType":"BillingAccount"}}'
    where = f'"bucket":{{"id":"{bucket["id"]}"}}'

    def post(path, text, key):
        return api.request(
            "POST",
            f"{prepay}/{path}",
            content=text,
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )

    def remaining():
        return decimals(api.get(f"{prepay}/bucket/{bucket['id']}"))["remainingValue"]["amount"]

    topup = (
        '{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},'
        f'"usageType":"monetary",{where},{owner},"paymentMethod":{{"id":"cash","name":"cash"}},'
        '"reason":"Cash at the desk"}'
    )
    first = post("topupBalance", topup, "ui-topup-1")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "completed"
    again = post("topupBalance", topup, "ui-topup-1")  # the uncertain-outcome retry
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert remaining() == Decimal("1250.50")

    adjust = (
        '{"@type":"AdjustBalance","amount":{"amount":%s,"units":"USD"},'
        '"usageType":"monetary",%s,"reason":"Correction"}'
    )
    credit = post("adjustBalance", adjust % ("12.50", where), "ui-adjust-1")
    debit = post("adjustBalance", adjust % ("-12.50", where), "ui-adjust-2")
    assert (credit.status_code, debit.status_code) == (201, 201), (credit.text, debit.text)
    assert (credit.json()["status"], debit.json()["status"]) == ("completed", "completed")
    assert remaining() == Decimal("1250.50")

    hold = post(
        "reserveBalance",
        '{"@type":"ReserveBalance","amount":{"amount":20.00,"units":"USD"},'
        f'"usageType":"monetary",{where},{owner},'
        '"validFor":{"endDateTime":"2026-10-06T00:00:00+03:00"}}',
        "ui-hold-1",
    )
    assert hold.status_code == 201, hold.text
    assert hold.json()["status"] == "created"
    assert remaining() == Decimal("1230.50")

    refused = post("adjustBalance", adjust % ("-5000.00", where), "ui-adjust-3")
    assert refused.status_code == 201, refused.text
    assert refused.json()["status"] == "failed" and refused.json()["reason"]
    assert remaining() == Decimal("1230.50")


def test_money_dialog_optional_fields_as_the_ui_sends_them(api, db_conn):
    """G9: number selection and payment metadata use the existing TMF contracts."""
    tx, _ = pair(api, db_conn)
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    own = api.get(f"{apidata.PRODUCT}?billingAccount.id={tx.id}&status=active&offset=0&limit=100")
    assert own.status_code == 200
    assert str(tx.number) in {
        ref["id"] for product in own.json() for ref in product.get("realizingResource", [])
    }
    number = api.get(f"/tmf-api/resourceInventory/v5/resource/{tx.number}")
    assert number.status_code == 200
    topup = api.request(
        "POST",
        f"{prepay}/topupBalance",
        content=(
            '{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},'
            f'"usageType":"monetary","bucket":{{"id":"{tx.bucket}"}},'
            f'"partyAccount":{{"id":"{tx.id}","@referredType":"BillingAccount"}},'
            '"paymentMethod":{"id":"cash","name":"cash"},'
            f'"logicalResource":[{{"id":"{tx.number}"}}]}}'
        ),
        headers={"Content-Type": "application/json", "Idempotency-Key": "ui-number-topup"},
    )
    assert topup.status_code == 201, topup.text
    assert topup.json()["status"] == "completed"
    assert topup.json()["logicalResource"][0]["id"] == str(tx.number)
    assert decimals(topup)["amount"]["amount"] == Decimal("1250.50")

    account = apidata.account(api)
    payment = api.post_raw(
        PAYMENT,
        f'{{"@type":"Payment","account":{{"id":"{account["id"]}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":1250.50},"paymentMethod":{"name":"bankTransfer"},'
        '"correlatorId":"BANK-77","description":"Cash at the desk"}',
    )
    assert payment.status_code == 201, payment.text
    stored = api.get(payment.json()["href"]).json()
    assert stored["correlatorId"] == "BANK-77"
    assert stored["description"] == "Cash at the desk"
    assert datetime.fromisoformat(stored["paymentDate"]) == api.clock.now()


def test_balance_action_writes_as_the_ui_sends_them(api):
    """cancelAction() / editNote() / removeAction() (api/history.ts), pinned in
    SubscriberCard.test.tsx: merge patches `{"status":"cancelled"}` and the note fields, and a
    DELETE without a body. A cancelled top-up reverses, a released hold frees funds, a refused
    action is deleted, and a top-up with held funds is a 409 with a message."""
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    account = apidata.account(api, ratingType="prepaid")
    bucket = api.get(f"{prepay}/bucket?partyAccount.id={account['id']}&offset=0&limit=100").json()[
        0
    ]
    where = {"bucket": {"id": bucket["id"]}}
    owner = {"id": account["id"], "@referredType": "BillingAccount"}
    money = {"units": "USD"}

    def stored(path, body, key):
        made = api.post(f"{prepay}/{path}", body, headers={"Idempotency-Key": key})
        assert made.status_code == 201, made.text
        return made.json()

    def values():
        found = api.get(f"{prepay}/bucket/{bucket['id']}").json()
        return found["remainingValue"]["amount"], found["reservedValue"]["amount"]

    def topup(amount, key):
        return stored(
            "topupBalance",
            {
                "@type": "TopupBalance",
                "amount": {**money, "amount": amount},
                "usageType": "monetary",
                "partyAccount": owner,
                "paymentMethod": {"id": "cash", "name": "cash"},
                **where,
            },
            key,
        )

    cancel = {"status": "cancelled"}
    first = topup(50, "w-topup-1")
    assert values() == (50, 0)
    cancelled = api.patch(f"{prepay}/topupBalance/{first['id']}", cancel)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert values() == (0, 0)

    second = topup(50, "w-topup-2")
    noted = api.patch(
        f"{prepay}/topupBalance/{second['id']}",
        {"status": "completed", "reason": "Cash at the desk", "description": None},
    )
    assert noted.status_code == 200 and noted.json()["reason"] == "Cash at the desk"
    hold = stored(
        "reserveBalance",
        {
            "@type": "ReserveBalance",
            "amount": {**money, "amount": 30},
            "usageType": "monetary",
            "partyAccount": owner,
            **where,
            "validFor": {"endDateTime": "2026-10-06T00:00:00+03:00"},
        },
        "w-hold-1",
    )
    assert values() == (20, 30)  # remainingValue is the spendable part
    blocked = api.patch(f"{prepay}/topupBalance/{second['id']}", cancel)
    assert blocked.status_code == 409 and blocked.json()["message"]
    # a hold's update schema requires `reason`: the UI repeats it or states one
    released = api.patch(
        f"{prepay}/reserveBalance/{hold['id']}",
        {"status": "cancelled", "reason": "Released by operator"},
    )
    assert released.status_code == 200 and released.json()["status"] == "cancelled"
    assert values() == (50, 0)
    renamed = api.patch(
        f"{prepay}/reserveBalance/{hold['id']}", {"status": "cancelled", "reason": "Deposit"}
    )
    assert renamed.status_code == 200 and renamed.json()["reason"] == "Deposit"

    credit = stored(
        "adjustBalance",
        {
            "@type": "AdjustBalance",
            "amount": {**money, "amount": 12.5},
            "usageType": "monetary",
            **where,
        },
        "w-adjust-1",
    )
    assert values() == (62.5, 0)
    undone = api.patch(f"{prepay}/adjustBalance/{credit['id']}", cancel)
    assert undone.status_code == 200 and undone.json()["status"] == "cancelled"
    assert values() == (50, 0)

    refused = stored(
        "adjustBalance",
        {
            "@type": "AdjustBalance",
            "amount": {**money, "amount": -5000},
            "usageType": "monetary",
            **where,
        },
        "w-adjust-2",
    )
    assert refused["status"] == "failed"
    removed = api.request("DELETE", f"{prepay}/adjustBalance/{refused['id']}")
    assert removed.status_code == 204 and removed.content == b""
    gone = api.get(f"{prepay}/balanceActionHistory/{refused['id']}")
    assert gone.status_code == 404
    assert values() == (50, 0)


def test_prepaid_transfer_as_the_ui_sends_it(api, db_conn):
    """transferRequest() and findReceiver() (api/transfer.ts), pinned in SubscriberCard.test.tsx:
    the receiver is found by number (inventory value filter, the product's account, that
    account's bucket); the exact body is accepted under an Idempotency-Key and a retry
    replays; the card then polls until the worker completes it. A refusal is stored with
    its explanation in `description`, while `reason` keeps the operator's own words."""
    tx, rx = pair(api, db_conn)
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    found = api.get("/tmf-api/resourceInventory/v5/resource?value=79991234001&offset=0&limit=100")
    assert [n["id"] for n in found.json()] == [str(rx.number)]
    products = api.get(
        f"/tmf-api/productInventory/v5/product?realizingResource.id={rx.number}&offset=0&limit=100"
    )
    assert [p["billingAccount"]["id"] for p in products.json()] == [rx.id]
    own = api.get(
        f"/tmf-api/productInventory/v5/product?billingAccount.id={tx.id}&status=active"
        "&offset=0&limit=100"
    )
    assert [r["id"] for p in own.json() for r in p["realizingResource"]] == [str(tx.number)]

    def body(amount):
        return (
            '{"@type":"TransferBalance","reason":"Customer asked",'
            '"channel":{"id":"admin-ui","name":"Admin UI"},'
            f'"logicalResource":[{{"id":"{tx.number}"}}],'
            f'"receiverLogicalResource":{{"id":"{rx.number}"}},'
            f'"amount":{{"amount":{amount},"units":"USD"}},"usageType":"monetary",'
            f'"bucket":{{"id":"{tx.bucket}"}},"receiverBucket":{{"id":"{rx.bucket}"}},'
            '"receiverBucketUsageType":"monetary"}'
        )

    def post(text, key):
        return api.request(
            "POST",
            f"{prepay}/transferBalance",
            content=text,
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )

    first = post(body("30.00"), "ui-transfer-1")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "created"
    again = post(body("30.00"), "ui-transfer-1")  # the uncertain-outcome retry
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    watched = f"{prepay}/transferBalance/{first.json()['id']}"
    assert api.get(watched).json()["status"] == "created"
    tick(db_conn, api.clock.now())
    assert api.get(watched).json()["status"] == "completed"

    refused = post(body("5000.00"), "ui-transfer-2")  # more than the sender can spend
    assert refused.status_code == 201, refused.text
    stored = refused.json()
    assert stored["status"] == "failed" and stored["reason"] == "Customer asked"
    assert stored["description"] and stored["description"] != stored["reason"]


def test_balance_action_history_reads_as_the_ui_makes_them(api, db_conn):
    """loadBalanceActions() / loadBalanceAction() (api/history.ts), pinned in
    SubscriberCard.test.tsx:
    the sender's card asks by partyAccount.id, the receiver's also by receiverBucket.id (the
    transfer it received), the receiving account is named through its bucket, and one action
    is fetched by id for the detail dialog."""
    tx, rx = pair(api, db_conn)
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    moved = api.post(
        f"{prepay}/transferBalance",
        {
            "@type": "TransferBalance",
            "reason": "Customer asked",
            "channel": {"id": "admin-ui", "name": "Admin UI"},
            "logicalResource": [{"id": str(tx.number)}],
            "receiverLogicalResource": {"id": str(rx.number)},
            "amount": {"amount": 10, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": tx.bucket},
            "receiverBucket": {"id": rx.bucket},
            "receiverBucketUsageType": "monetary",
        },
        headers={"Idempotency-Key": "ui-history-1"},
    ).json()
    assert "id" in moved, moved
    history = f"{prepay}/balanceActionHistory"
    sent = api.get(f"{history}?partyAccount.id={tx.id}&offset=0&limit=100")
    assert [a["id"] for a in sent.json()] == [moved["id"]]
    assert sent.json()[0]["@type"] == "TransferBalance"
    assert sent.json()[0]["receiverBucket"]["id"] == rx.bucket
    owner = api.get(f"{prepay}/bucket/{rx.bucket}").json()["partyAccount"]
    assert owner["id"] == rx.id and owner["name"]
    got = api.get(f"{history}?receiverBucket.id={rx.bucket}&offset=0&limit=100")
    assert [a["id"] for a in got.json()] == [moved["id"]]
    assert got.json()[0]["partyAccount"]["id"] == tx.id  # the sender, named for the receiver
    assert api.get(f"{history}?receiverBucket.id={tx.bucket}&offset=0&limit=100").json() == []
    one = api.get(f"{history}/{moved['id']}").json()
    assert one["transferCost"]["value"] == 0 and one["costOwner"] == "originator"
    assert one["status"] == "created" and one["requestedDate"]


def test_balances_screen_reads_as_the_ui_makes_them(api, db_conn):
    """api/balances.ts and api/history.ts loadKind* of slice 22 (pinned in Balances.test.tsx):
    the buckets tab joins the bucket list with the accumulated balances (all, or one account and
    a status), a postpaid account has no bucket and never appears, a kind's list is filtered by
    partyAccount.id and status, and the detail is the kind's GET with the receiver bucket
    resolved for a transfer."""
    tx, rx = pair(api, db_conn)
    postpaid = apidata.account(api)  # no prepaid rating: no bucket
    prepay = "/tmf-api/prepayBalanceManagement/v4"
    moved = api.post(
        f"{prepay}/transferBalance",
        {
            "@type": "TransferBalance",
            "reason": "Customer asked",
            "channel": {"id": "admin-ui", "name": "Admin UI"},
            "logicalResource": [{"id": str(tx.number)}],
            "receiverLogicalResource": {"id": str(rx.number)},
            "amount": {"amount": 10, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": tx.bucket},
            "receiverBucket": {"id": rx.bucket},
            "receiverBucketUsageType": "monetary",
        },
        headers={"Idempotency-Key": "ui-balances-1"},
    ).json()
    assert "id" in moved, moved
    page = "offset=0&limit=100"
    buckets = api.get(f"{prepay}/bucket?{page}").json()
    assert {b["id"] for b in buckets} >= {tx.bucket, rx.bucket}
    assert postpaid["id"] not in {b["partyAccount"]["id"] for b in buckets}
    mine = api.get(f"{prepay}/bucket?partyAccount.id={tx.id}&status=active&{page}").json()
    assert [b["id"] for b in mine] == [tx.bucket]
    assert mine[0]["remainingValue"]["amount"] == 40
    suspended = api.get(f"{prepay}/bucket?status=suspended&{page}").json()
    assert all(b["status"] == "suspended" for b in suspended)
    totals = api.get(f"{prepay}/accumulatedBalance?{page}").json()
    assert {t["partyAccount"]["id"] for t in totals} >= {tx.id, rx.id}
    one_total = api.get(f"{prepay}/accumulatedBalance?partyAccount.id={tx.id}&{page}").json()
    assert [t["id"] for t in one_total] == [
        next(t["id"] for t in totals if t["partyAccount"]["id"] == tx.id)
    ]
    assert api.get(f"{prepay}/bucket/{tx.bucket}").json()["partyAccount"]["id"] == tx.id
    total_detail = api.get(f"{prepay}/accumulatedBalance/{one_total[0]['id']}")
    assert total_detail.status_code == 200
    assert total_detail.json()["partyAccount"]["id"] == tx.id
    assert total_detail.json()["totalBalance"] == one_total[0]["totalBalance"]
    assert one_total[0]["totalBalance"] == {"amount": 40, "units": "USD"}
    # each kind's list, by account and status; the transfer is the sender's, created
    sent = api.get(f"{prepay}/transferBalance?partyAccount.id={tx.id}&{page}").json()
    assert [t["id"] for t in sent] == [moved["id"]]
    assert api.get(f"{prepay}/transferBalance?partyAccount.id={rx.id}&{page}").json() == []
    created = api.get(f"{prepay}/transferBalance?status=created&{page}").json()
    assert moved["id"] in {t["id"] for t in created}
    assert api.get(f"{prepay}/transferBalance?status=failed&{page}").json() == []
    for kind in ("topupBalance", "adjustBalance", "reserveBalance"):
        assert api.get(f"{prepay}/{kind}?partyAccount.id={tx.id}&{page}").status_code == 200
        assert api.get(f"{prepay}/{kind}?status=created&{page}").status_code == 200
    # the detail: the transfer by id and its receiver bucket's owner (named through it)
    one = api.get(f"{prepay}/transferBalance/{moved['id']}").json()
    assert one["@type"] == "TransferBalance" and one["receiverBucket"]["id"] == rx.bucket
    owner = api.get(f"{prepay}/bucket/{rx.bucket}").json()["partyAccount"]
    assert owner["id"] == rx.id and owner["name"]


def test_prepaid_order_and_command_reads_as_the_ui_makes_them(api, db_conn, monkeypatch):
    """OrderWatch / FailedCommands (api/commands.ts): a prepaid add answers `inProgress`, the UI
    follows it through the command that has the order's id, lists an account's technically
    failed commands with the query it sends, and retries one with a POST without a body; a
    second retry is the 409 it handles by looking again."""
    off, account = shop(api, db_conn)
    order = purchase(api, off, account)
    assert order["state"] == "inProgress"
    command = api.get(f"/internal/v1/prepaid/command/{order['id']}").json()
    assert (command["id"], command["kind"], command["state"]) == (order["id"], "order", "pending")
    # due today: the card's OrderWatch calls anything later "scheduled"
    today = api.get("/internal/v1/context").json()["businessDate"]
    assert command["needs_topup"] is None and command["dueDate"] == today

    _, _, stuck = failed(db_conn, monkeypatch)
    owner = db_conn.execute(
        "SELECT billing_account_id FROM prepaid_command WHERE id = %s", (stuck["id"],)
    ).fetchone()[0]
    listed = api.get(
        f"/internal/v1/prepaid/command?billingAccount.id={owner}&state=failedTechnical"
        "&offset=0&limit=100"
    )
    assert [c["id"] for c in listed.json()] == [str(stuck["id"])]
    assert listed.json()[0]["error"] and listed.json()[0]["nextRetryAt"]
    assert (
        api.get(
            f"/internal/v1/prepaid/command?billingAccount.id={account['id']}&state=failedTechnical"
        ).json()
        == []
    )
    # the Prepaid commands screen (api/commands.ts): a page of 50 with the state and account
    # filters, and the sidebar badge's one-row count read from X-Total-Count
    page = api.get(
        f"/internal/v1/prepaid/command?billingAccount.id={owner}&state=failedTechnical"
        "&offset=0&limit=50"
    )
    assert [c["id"] for c in page.json()] == [str(stuck["id"])]
    assert page.headers["X-Total-Count"] == "1"
    badge = api.get("/internal/v1/prepaid/command?state=failedTechnical&limit=1")
    assert len(badge.json()) == 1 and int(badge.headers["X-Total-Count"]) >= 1
    everything = api.get("/internal/v1/prepaid/command?offset=0&limit=50")
    assert everything.status_code == 200 and str(stuck["id"]) in [
        c["id"] for c in everything.json()
    ]
    assert api.get("/internal/v1/prepaid/command?state=bogus").status_code == 400
    retried = api.request("POST", f"/internal/v1/prepaid/command/{stuck['id']}/retry")
    assert retried.status_code == 202 and retried.json()["state"] == "pending"
    again = api.request("POST", f"/internal/v1/prepaid/command/{stuck['id']}/retry")
    assert again.status_code == 409


def test_edit_subscriber_as_the_ui_patches_it(api):
    """updateSubscriber(): merge patches of only the changed attributes; the credit limit as
    an exact token or null."""
    made = create_subscriber_as_the_ui_does(api)
    party = api.patch(made["party"]["href"], {"familyName": "Kuznetsova-Petrova"})
    assert party.status_code == 200 and party.json()["familyName"] == "Kuznetsova-Petrova"
    customer = api.patch(made["customer"]["href"], {"status": "Inactive"})
    assert customer.status_code == 200 and customer.json()["status"] == "Inactive"
    exact = api.request(
        "PATCH",
        made["account"]["href"],
        content='{"creditLimit":{"unit":"USD","value":2000.10}}',
        headers={"Content-Type": "application/merge-patch+json"},
    )
    assert exact.status_code == 200, exact.text
    assert decimals(exact)["creditLimit"]["value"] == Decimal("2000.10")
    removed = api.patch(made["account"]["href"], {"state": "Suspended", "creditLimit": None})
    assert removed.status_code == 200, removed.text
    assert removed.json()["state"] == "Suspended" and "creditLimit" not in removed.json()


def ui_product_order(account, item, day):
    """orderProductChange(): the order the UI sends for a suspend, resume or end on `day`."""
    return {
        "@type": "ProductOrder",
        "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
        "requestedStartDate": f"{day}T12:00:00{OFFSET}",
        "productOrderItem": [{"@type": "ProductOrderItem", "id": "1", **item}],
    }


def ui_status_item(product_id, status):
    ref = {"id": product_id, "@type": "ProductRef"}
    return {"action": "modify", "product": {**ref, "status": status}}


def test_postpaid_product_actions_as_the_ui_sends_them(api):
    """ProductChangeDialog (api/bss.ts orderProductChange): a dated suspend and resume, an
    end on a date, and the Undo of a pending one (cancelProductOrder with the order ref that
    pendingChanges names)."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    placed = apidata.order(api, account, apidata.add_item(offering))
    product = apidata.product_of(api, placed)
    other = apidata.product_of(api, apidata.order(api, account, apidata.add_item(offering)))
    listing = f"/internal/v1/billingAccount/{account['id']}/products"

    suspend = api.post(
        ORDER,
        ui_product_order(account, ui_status_item(product["id"], "suspended"), "2026-10-20"),
    )
    assert suspend.status_code == 201 and suspend.json()["state"] == "completed", suspend.text
    pending = next(p for p in api.get(listing).json() if p["id"] == product["id"])["pendingChanges"]
    assert [(c["kind"], c["date"], c["status"]) for c in pending] == [
        ("status", "2026-10-20", "suspended")
    ]
    assert pending[0]["productOrder"]["id"] == suspend.json()["id"]
    cancelled = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": pending[0]["productOrder"]["id"], "@type": "ProductOrderRef"},
        },
    )
    assert cancelled.status_code == 201, cancelled.text
    assert (
        next(p for p in api.get(listing).json() if p["id"] == product["id"])["pendingChanges"] == []
    )

    # suspended today, resumed on a later date
    now = api.post(
        ORDER, ui_product_order(account, ui_status_item(product["id"], "suspended"), "2026-10-02")
    )
    assert now.json()["state"] == "completed", now.text
    assert api.get(product["href"]).json()["status"] == "suspended"
    resume = api.post(
        ORDER, ui_product_order(account, ui_status_item(product["id"], "active"), "2026-10-25")
    )
    assert resume.status_code == 201 and resume.json()["state"] == "completed", resume.text

    delete = {"action": "delete", "product": {"id": other["id"], "@type": "ProductRef"}}
    end = api.post(ORDER, ui_product_order(account, delete, "2026-11-03"))
    assert end.status_code == 201 and end.json()["state"] == "completed", end.text
    assert api.get(other["href"]).json()["status"] == "pendingTerminate"


def test_prepaid_product_actions_as_the_ui_sends_them(api, db_conn):
    """The prepaid bodies are the same: a suspend dated today completes in the order, the
    resume comes on a later day, an end on a date is accepted as inProgress for the worker."""
    off, account = shop(api, db_conn, numbers=False)
    purchase(api, off, account, numbers=False)
    tick(db_conn, api.clock.now())
    ids = [r[0] for r in db_conn.execute("SELECT id::text FROM product ORDER BY id").fetchall()]
    today = api.clock.now().date()

    suspend = api.post(
        ORDER, ui_product_order(account, ui_status_item(ids[0], "suspended"), today.isoformat())
    )
    assert suspend.status_code == 201 and suspend.json()["state"] == "completed", suspend.text
    api.clock.set(api.clock.now() + timedelta(days=1))
    later = api.clock.now().date().isoformat()
    resume = api.post(ORDER, ui_product_order(account, ui_status_item(ids[0], "active"), later))
    assert resume.status_code == 201, resume.text
    assert resume.json()["state"] in ("completed", "inProgress"), resume.text

    delete = {"action": "delete", "product": {"id": ids[1], "@type": "ProductRef"}}
    end = api.post(ORDER, ui_product_order(account, delete, later))
    assert end.status_code == 201 and end.json()["state"] == "inProgress", end.text


def test_prepaid_dated_suspend_as_the_ui_sends_it(api, db_conn):
    """ProductChangeDialog on a prepaid product: the same body dated later is accepted as
    inProgress, shows as a pending status change, and the Undo cancels it before its day."""
    off, account = shop(api, db_conn, numbers=False)
    purchase(api, off, account, numbers=False)
    tick(db_conn, api.clock.now())
    product_id = db_conn.execute("SELECT id::text FROM product ORDER BY id").fetchone()[0]
    listing = f"/internal/v1/billingAccount/{account['id']}/products"

    suspend = api.post(
        ORDER,
        ui_product_order(account, ui_status_item(product_id, "suspended"), "2026-10-12"),
    )
    assert suspend.status_code == 201 and suspend.json()["state"] == "inProgress", suspend.text
    pending = next(p for p in api.get(listing).json() if p["id"] == product_id)["pendingChanges"]
    assert [(c["kind"], c["date"], c["status"]) for c in pending] == [
        ("status", "2026-10-12", "suspended")
    ]
    assert pending[0]["productOrder"]["id"] == suspend.json()["id"]
    undo = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {"@type": "CancelProductOrder", "productOrder": {"id": suspend.json()["id"]}},
    )
    assert undo.status_code == 201, undo.text
    assert next(p for p in api.get(listing).json() if p["id"] == product_id)["pendingChanges"] == []


def ui_offering_item(product_id, offering_id):
    """orderOfferingChange(): a modify item with the new offering, its href, and no status."""
    return {
        "action": "modify",
        "product": {"id": product_id, "@type": "ProductRef"},
        "productOffering": {
            "id": offering_id,
            "href": f"{CATALOG}/productOffering/{offering_id}",
            "@type": "ProductOfferingRef",
        },
    }


def test_offering_change_current_specification_read_as_the_ui_sends_it(api):
    offering = apidata.sellable_offering(api)
    response = api.get(f"{apidata.CATALOG}/productOffering/{offering['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["productSpecification"]["id"] == offering["productSpecification"]["id"]


def test_postpaid_offering_change_as_the_ui_sends_it(api):
    """ChangeOfferingDialog: the preview read for the chosen day, the modify order on the
    product's next charge date, and the pending change it leaves (with its Undo order)."""
    offering = apidata.sellable_offering(api)
    other = apidata.sellable_offering(api, value=900, name="Fiber Plus")
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    product = apidata.product_of(api, apidata.order(api, account, apidata.add_item(offering)))
    listing = f"/internal/v1/billingAccount/{account['id']}/products"
    row = next(p for p in api.get(listing).json() if p["id"] == product["id"])
    day = row["nextChargeDate"]
    assert day is not None and row["pendingChanges"] == []

    preview = api.get(
        f"/internal/v1/billingAccount/{account['id']}/firstCharge"
        f"?productOffering.id={other['id']}&startDate={day}"
    )
    assert preview.status_code == 200, preview.text

    changed = api.post(
        ORDER, ui_product_order(account, ui_offering_item(product["id"], other["id"]), day)
    )
    assert changed.status_code == 201 and changed.json()["state"] == "completed", changed.text
    pending = next(p for p in api.get(listing).json() if p["id"] == product["id"])["pendingChanges"]
    assert [(c["kind"], c["date"], c["productOffering"]["id"]) for c in pending] == [
        ("offering", day, other["id"])
    ]
    assert pending[0]["productOrder"]["id"] == changed.json()["id"]


def test_prepaid_offering_change_as_the_ui_sends_it(api, db_conn):
    """The prepaid dialog fixes the day at max(paidThrough, today): the same body completes
    and leaves a pending change dated paidThrough; any other day is refused and shown."""
    other = apidata.sellable_offering(api, value=5, name="Fiber Lite")
    off, account = shop(api, db_conn, numbers=False)
    purchase(api, off, account, numbers=False)
    tick(db_conn, api.clock.now())
    product_id, through = db_conn.execute(
        "SELECT product_id::text, upper(period) FROM funded_interval ORDER BY product_id LIMIT 1"
    ).fetchone()
    day = through.isoformat()

    wrong = api.post(
        ORDER,
        ui_product_order(
            account,
            ui_offering_item(product_id, other["id"]),
            (through + timedelta(days=1)).isoformat(),
        ),
    )
    assert "offeringChangeNotAtRenewal" in wrong.text, wrong.text

    changed = api.post(
        ORDER, ui_product_order(account, ui_offering_item(product_id, other["id"]), day)
    )
    assert changed.status_code == 201 and changed.json()["state"] == "completed", changed.text
    listing = f"/internal/v1/billingAccount/{account['id']}/products"
    row = next(p for p in api.get(listing).json() if p["id"] == product_id)
    assert [(c["kind"], c["date"]) for c in row["pendingChanges"]] == [("offering", day)]


def test_orders_screen_reads_as_the_ui_makes_them(api):
    """loadOrderPage() / loadCancellations() (api/orders.ts), pinned in Orders.test.tsx: a
    page of 50 with the account and state filters and an order's cancellations by
    productOrder.id. The screen has no writes."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    added = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                }
            ],
        },
    ).json()
    page = api.get(f"{ORDER}?billingAccount.id={account['id']}&state=completed&offset=0&limit=50")
    assert [o["id"] for o in page.json()] == [added["id"]] and page.headers["X-Total-Count"] == "1"
    item = page.json()[0]["productOrderItem"][0]
    assert item["productOffering"]["name"] and page.json()[0]["billingAccount"]["name"]
    assert item["itemTotalPrice"][0]["price"]["taxIncludedAmount"]["value"] is not None
    assert page.json()[0]["completionDate"] and page.json()[0]["creationDate"]
    # G12: count first, then read the tail of the oldest-first API and reverse it.
    probe = api.get(
        f"{ORDER}?billingAccount.id={account['id']}&state=completed&offset=0&limit=1&fields=id"
    )
    assert probe.status_code == 200 and probe.headers["X-Total-Count"] == "1"
    tail = api.get(f"{ORDER}?billingAccount.id={account['id']}&state=completed&offset=0&limit=1")
    assert [o["id"] for o in reversed(tail.json())] == [added["id"]]
    detail = api.get(f"{ORDER}/{added['id']}")
    assert detail.status_code == 200 and detail.json()["id"] == added["id"]
    product_id = detail.json()["productOrderItem"][0]["product"]["id"]
    product = api.get(f"/tmf-api/productInventory/v5/product/{product_id}")
    assert product.status_code == 200 and product.json()["name"]
    assert api.get(f"{ORDER}?state=rejected&offset=0&limit=50").json() == []
    cancels = api.get(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder"
        f"?productOrder.id={added['id']}&offset=0&limit=100"
    )
    assert cancels.status_code == 200 and cancels.json() == []


def test_order_writes_replay_as_the_ui_sends_them(api):
    """The order dialog's writes (api/bss.ts, pinned in Orders.test.tsx and
    SubscriberCard.test.tsx): save as a draft under an Idempotency-Key, replace its item and
    start date, change only some details (null removes one), submit with state acknowledged,
    DELETE a draft, cancel an order with and without a reason."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    ref = {"id": account["id"], "@type": "BillingAccountRef"}

    def item(item_id="1"):
        return {
            "@type": "ProductOrderItem",
            "id": item_id,
            "action": "add",
            "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
        }

    draft_body = {
        "@type": "ProductOrder",
        "billingAccount": ref,
        "requestedStartDate": "2026-10-05T12:00:00+03:00",
        "requestedInitialState": "draft",
        "productOrderItem": [item()],
    }
    first = api.post(ORDER, draft_body, headers={"Idempotency-Key": "ui-draft-key-0001"})
    assert first.status_code == 201 and first.json()["state"] == "draft", first.text
    again = api.post(ORDER, draft_body, headers={"Idempotency-Key": "ui-draft-key-0001"})
    assert again.json()["id"] == first.json()["id"]
    url = f"{ORDER}/{first.json()['id']}"

    replaced = api.patch(
        url,
        {"productOrderItem": [item("1")], "requestedStartDate": "2026-10-06T12:00:00+03:00"},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["state"] == "draft"
    assert replaced.json()["requestedStartDate"] == "2026-10-05T21:00:00Z"  # 10-06 in +03:00

    described = api.patch(url, {"description": "New text", "priority": "0", "category": "retail"})
    assert described.status_code == 200 and described.json()["priority"] == "0"
    cleared = api.patch(url, {"category": None})
    assert cleared.status_code == 200 and "category" not in cleared.json()
    assert cleared.json()["description"] == "New text"
    reloaded = api.get(url)
    assert reloaded.status_code == 200 and reloaded.json()["description"] == "New text"
    assert reloaded.json()["priority"] == "0" and "category" not in reloaded.json()

    submitted = api.patch(url, {"state": "acknowledged"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] in ("completed", "inProgress", "rejected")

    spare = api.post(ORDER, {**draft_body, "requestedStartDate": "2026-10-07T12:00:00+03:00"})
    assert spare.status_code == 201
    assert api.delete(f"{ORDER}/{spare.json()['id']}").status_code == 204
    assert api.get(f"{ORDER}/{spare.json()['id']}").status_code == 404

    cancels = "/tmf-api/productOrderingManagement/v5/cancelProductOrder"
    other = api.post(ORDER, {**draft_body, "requestedStartDate": "2026-10-08T12:00:00+03:00"})
    assert other.status_code == 201
    plain = api.post(
        cancels,
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": other.json()["id"], "@type": "ProductOrderRef"},
        },
    )
    assert plain.status_code == 201, plain.text
    last = api.post(ORDER, {**draft_body, "requestedStartDate": "2026-10-09T12:00:00+03:00"})
    with_reason = api.post(
        cancels,
        {
            "@type": "CancelProductOrder",
            "productOrder": {"id": last.json()["id"], "@type": "ProductOrderRef"},
            "cancellationReason": "Customer left",
        },
    )
    assert with_reason.status_code == 201, with_reason.text
    assert with_reason.json()["cancellationReason"] == "Customer left"


def test_party_and_customer_writes_as_the_ui_sends_them(api):
    """patchParty(), patchCustomer(), deleteParty() and deleteCustomer() and the list
    requests of loadParties(): bodies pinned in Parties.test.tsx."""
    person = apidata.individual(api, "Olga", "Kuznetsova")
    org = apidata.organization(api, "Lone Ltd")
    buyer = apidata.customer(api, apidata.individual(api, "Ivan", "Petrov"))
    held = apidata.account(api, buyer)
    # the list requests of the Parties screen: fields and paging are accepted
    party = "/tmf-api/partyManagement/v5"
    accounts = "/tmf-api/accountManagement/v5"
    for url in (
        f"{party}/individual?offset=0&limit=100",
        f"{party}/organization?offset=0&limit=100",
        "/tmf-api/customer/v5/customer?fields=name,status,engagedParty&offset=0&limit=100",
        f"{accounts}/billingAccount?fields=name,relatedParty&offset=0&limit=100",
        f"{accounts}/financialAccount?fields=relatedParty&offset=0&limit=100",
        f"{accounts}/partyAccount?fields=relatedParty&offset=0&limit=100",
        f"{accounts}/settlementAccount?fields=relatedParty&offset=0&limit=100",
    ):
        assert api.get(url).status_code == 200, url
    # a merge patch of only the changed names
    patched = api.patch(person["href"], {"familyName": "Kuznetsova-Lee"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["givenName"] == "Olga"
    assert patched.json()["familyName"] == "Kuznetsova-Lee"
    assert api.patch(org["href"], {"name": "Lone Holdings"}).json()["name"] == "Lone Holdings"
    cust = apidata.customer(api, person)
    changed = api.patch(cust["href"], {"name": "Olga K.", "status": "Inactive"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "Inactive"
    # a party with a customer, and a customer with a billing account, are 409 (the UI hides
    # Delete from the loaded data and still shows this message)
    assert api.delete(person["href"]).status_code == 409
    assert api.delete(buyer["href"]).status_code == 409
    assert held["id"]
    assert api.delete(cust["href"]).status_code == 204
    assert api.delete(person["href"]).status_code == 204
    assert api.delete(org["href"]).status_code == 204
    assert api.get(org["href"]).status_code == 404


def test_g10_party_customer_and_subscriber_writes_as_the_ui_sends_them(api):
    """G10 createParty(), partyChanges(), createCustomer() and createSubscriber() contact
    media: exact bodies pinned in Parties.test.tsx and Subscribers.test.tsx."""
    party = "/tmf-api/partyManagement/v5"
    individual = api.post_raw(
        f"{party}/individual",
        '{"@type":"Individual","givenName":"Anna","familyName":"Lee","status":"validated",'
        '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",'
        '"emailAddress":"anna@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",'
        '"phoneNumber":"+7 900 000-00-00"},{"@type":"GeographicAddressContactMedium",'
        '"contactType":"postalAddress","street1":"Tverskaya 1","city":"Moscow",'
        '"postCode":"101000","country":"Russia"}],"birthDate":"1990-01-02T00:00:00+03:00"}',
    )
    assert individual.status_code == 201, individual.text
    assert individual.json()["contactMedium"][2]["contactType"] == "postalAddress"
    assert individual.json()["birthDate"] == "1990-01-01T21:00:00Z"

    organization = api.post_raw(
        f"{party}/organization",
        '{"@type":"Organization","name":"New Co","status":"closed",'
        '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",'
        '"emailAddress":"office@example.org"}]}',
    )
    assert organization.status_code == 201, organization.text
    assert organization.json()["status"] == "closed"

    edited = api.patch_raw(
        individual.json()["href"],
        '{"birthDate":"1985-04-03T00:00:00+03:00","status":"validated",'
        '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",'
        '"emailAddress":"olga@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",'
        '"phoneNumber":"+1 555 0100"},{"@type":"GeographicAddressContactMedium",'
        '"contactType":"postalAddress","street1":"Main 2","city":"Boston",'
        '"postCode":"02108","country":"US"}],"individualIdentification":['
        '{"@type":"IndividualIdentification","identificationType":"passport",'
        '"identificationId":"P123"}],"externalReference":[{"@type":"ExternalIdentifier",'
        '"externalIdentifierType":"crmId","id":"CRM-9"}]}',
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["individualIdentification"][0]["identificationId"] == "P123"
    assert edited.json()["externalReference"][0]["id"] == "CRM-9"

    customer = api.post_raw(
        CUSTOMERS,
        '{"@type":"Customer","name":"Lone Ltd","status":"Active",'
        '"engagedParty":{"id":"'
        + organization.json()["id"]
        + '","@type":"PartyRef","@referredType":"Organization"}}',
    )
    assert customer.status_code == 201, customer.text
    assert customer.json()["engagedParty"]["id"] == organization.json()["id"]
    assert customer.json()["status"] == "Active"

    subscriber_party = api.post_raw(
        f"{party}/individual",
        '{"@type":"Individual","givenName":"Olga","familyName":"K",'
        '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",'
        '"emailAddress":"olga@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",'
        '"phoneNumber":"+1 555 0100"}]}',
    )
    assert subscriber_party.status_code == 201, subscriber_party.text
    assert subscriber_party.json()["contactMedium"][1]["phoneNumber"] == "+1 555 0100"


def test_financial_account_writes_and_links_as_the_ui_sends_them(api):
    """createBody(), patchBody() of api/financial.ts, the link in updateSubscriber() and the
    deletes: bodies pinned in FinancialAccounts.test.tsx and SubscriberCard.test.tsx."""
    fin = "/tmf-api/accountManagement/v5/financialAccount"
    org = apidata.organization(api, "Romashka LLC")
    person = apidata.individual(api, "Olga", "Kuznetsova")
    owner = {
        "role": "owner",
        "partyOrPartyRole": {"id": org["id"], "@type": "PartyRef", "@referredType": "Organization"},
        "@type": "RelatedPartyRefOrPartyRoleRef",
    }
    # the lists of the forms: owners, and one owner's financial accounts
    for url in (
        f"{PARTY}/individual?offset=0&limit=100",
        f"{PARTY}/organization?offset=0&limit=100",
        f"{fin}?relatedParty.id={org['id']}&offset=0&limit=100",
    ):
        assert api.get(url).status_code == 200, url
    created = api.post_raw(
        fin,
        '{"@type":"FinancialAccount","name":"Holding","relatedParty":['
        + json.dumps(owner, separators=(",", ":"))
        + '],"state":"Active","accountType":"Business","description":"All lines",'
        '"creditLimit":{"unit":"USD","value":1000.10}}',
    )
    assert created.status_code == 201, created.text
    group = created.json()
    assert decimals(created)["creditLimit"]["value"] == Decimal("1000.10")
    # an edit: only the changed fields; a cleared optional field is null
    edited = api.patch_raw(
        group["href"],
        '{"accountType":null,"description":"Spare","state":"Suspended",'
        '"creditLimit":{"unit":"USD","value":250.00}}',
    )
    assert edited.status_code == 200, edited.text
    assert "accountType" not in edited.json()
    assert edited.json()["state"] == "Suspended"
    assert decimals(edited)["creditLimit"]["value"] == Decimal("250.00")
    # link and unlink from the subscriber's Edit form
    buyer = apidata.customer(api, org)
    held = apidata.account(api, buyer)
    ref = {"id": group["id"], "@type": "FinancialAccountRef", "@referredType": "FinancialAccount"}
    linked = api.patch(held["href"], {"financialAccount": ref})
    assert linked.status_code == 200, linked.text
    assert linked.json()["financialAccount"]["id"] == group["id"]
    # linked: delete is 409 (the UI hides it), the owner cannot change
    assert api.delete(group["href"]).status_code == 409
    moved = api.patch(
        group["href"],
        {
            "relatedParty": [
                {
                    "role": "owner",
                    "partyOrPartyRole": {
                        "id": person["id"],
                        "@type": "PartyRef",
                        "@referredType": "Individual",
                    },
                    "@type": "RelatedPartyRefOrPartyRoleRef",
                }
            ]
        },
    )
    assert moved.status_code == 409
    # another owner's financial account is a 422 on the link
    other = api.post(
        fin,
        {
            "@type": "FinancialAccount",
            "name": "Personal",
            "relatedParty": [
                {
                    "role": "owner",
                    "partyOrPartyRole": {
                        "id": person["id"],
                        "@type": "PartyRef",
                        "@referredType": "Individual",
                    },
                    "@type": "RelatedPartyRefOrPartyRoleRef",
                }
            ],
        },
    ).json()
    foreign = {**ref, "id": other["id"]}
    assert api.patch(held["href"], {"financialAccount": foreign}).status_code == 422
    unlinked = api.patch(held["href"], {"financialAccount": None})
    assert unlinked.status_code == 200 and "financialAccount" not in unlinked.json()
    assert api.delete(group["href"]).status_code == 204
    assert api.delete(other["href"]).status_code == 204
    # an account without history deletes; one with a payment is a 409 (close it instead)
    busy = apidata.account(api, apidata.customer(api, apidata.individual(api, "Anna", "Lee")))
    apidata.payment(api, busy)
    refused = api.delete(busy["href"])
    assert refused.status_code == 409 and "close it instead" in refused.json()["message"]
    assert api.delete(held["href"]).status_code == 204
    assert api.get(held["href"]).status_code == 404


def test_party_and_settlement_account_writes_as_the_ui_sends_them(api):
    """createBody(kind), patchBody() of api/financial.ts for both resources and the deletes:
    bodies pinned in PartyAccounts.test.tsx."""
    acc = "/tmf-api/accountManagement/v5"
    org = apidata.organization(api, "Romashka LLC")
    person = apidata.individual(api, "Olga", "Kuznetsova")
    owner = {
        "role": "owner",
        "partyOrPartyRole": {"id": org["id"], "@type": "PartyRef", "@referredType": "Organization"},
        "@type": "RelatedPartyRefOrPartyRoleRef",
    }
    owner_json = json.dumps(owner, separators=(",", ":"))
    group = api.post(
        f"{acc}/financialAccount",
        {"@type": "FinancialAccount", "name": "Group", "relatedParty": [owner]},
    ).json()
    ref = {"id": group["id"], "@type": "FinancialAccountRef", "@referredType": "FinancialAccount"}
    ref_json = json.dumps(ref, separators=(",", ":"))
    # the lists of the tabs and of the link select
    for url in (
        f"{acc}/partyAccount?offset=0&limit=100",
        f"{acc}/settlementAccount?offset=0&limit=100",
        f"{acc}/financialAccount?relatedParty.id={org['id']}&offset=0&limit=100",
    ):
        assert api.get(url).status_code == 200, url
    created = api.post_raw(
        f"{acc}/partyAccount",
        '{"@type":"PartyAccount","name":"Treasury","relatedParty":['
        + owner_json
        + '],"state":"Active","financialAccount":'
        + ref_json
        + "}",
    )
    assert created.status_code == 201, created.text
    party = created.json()
    assert party["@type"] == "PartyAccount" and party["financialAccount"]["id"] == group["id"]
    settlement = api.post_raw(
        f"{acc}/settlementAccount",
        '{"@type":"SettlementAccount","name":"Payouts","relatedParty":['
        + owner_json
        + '],"state":"Active","creditLimit":{"unit":"EUR","value":10.00}}',
    )
    assert settlement.status_code == 201, settlement.text
    assert settlement.json()["@type"] == "SettlementAccount"
    assert "financialAccount" not in settlement.json()
    # an edit: a cleared description and the link removed; then linked again
    unlinked = api.patch_raw(party["href"], '{"description":null,"financialAccount":null}')
    assert unlinked.status_code == 200, unlinked.text
    assert "financialAccount" not in unlinked.json()
    linked = api.patch_raw(settlement.json()["href"], '{"financialAccount":' + ref_json + "}")
    assert linked.status_code == 200 and linked.json()["financialAccount"]["id"] == group["id"]
    # a new owner without a link (the UI resets it)
    new_owner = {
        "role": "owner",
        "partyOrPartyRole": {
            "id": person["id"],
            "@type": "PartyRef",
            "@referredType": "Individual",
        },
        "@type": "RelatedPartyRefOrPartyRoleRef",
    }
    moved = api.patch(
        settlement.json()["href"], {"relatedParty": [new_owner], "financialAccount": None}
    )
    assert moved.status_code == 200, moved.text
    assert api.patch(party["href"], {"relatedParty": [new_owner]}).status_code == 200
    # nothing refers to them: both delete
    assert api.delete(party["href"]).status_code == 204
    assert api.delete(settlement.json()["href"]).status_code == 204
    assert api.get(party["href"]).status_code == 404


def test_billing_settings_writes_and_bill_structure_as_the_ui_sends_them(api):
    """api/billing.ts createBody/patchBody, the deletes, and billStructurePatch() of
    api/bss.ts: bodies pinned in BillingSettings.test.tsx and SubscriberCard.test.tsx."""
    acc = "/tmf-api/accountManagement/v5"
    for url in (
        f"{acc}/billingCycleSpecification?offset=0&limit=100",
        f"{acc}/billFormat?offset=0&limit=100",
        f"{acc}/billPresentationMedia?offset=0&limit=100",
    ):
        assert api.get(url).status_code == 200, url
    cycle = api.post_raw(
        f"{acc}/billingCycleSpecification",
        '{"@type":"BillingCycleSpecification","name":"Quarter end","description":"Names only"}',
    )
    assert cycle.status_code == 201, cycle.text
    spare = api.post_raw(
        f"{acc}/billingCycleSpecification",
        '{"@type":"BillingCycleSpecification","name":"Spare"}',
    )
    assert spare.status_code == 201, spare.text
    fmt = api.post_raw(
        f"{acc}/billFormat",
        '{"@type":"BillFormat","name":"JSON summary","description":"Totals"}',
    )
    assert fmt.status_code == 201, fmt.text
    medium = api.post_raw(
        f"{acc}/billPresentationMedia", '{"@type":"BillPresentationMedia","name":"SMS"}'
    )
    assert medium.status_code == 201, medium.text
    other = api.post_raw(
        f"{acc}/billPresentationMedia", '{"@type":"BillPresentationMedia","name":"Post"}'
    )
    assert other.status_code == 201, other.text
    # edits: a cleared description, a rename with a new description
    cleared = api.patch_raw(
        cycle.json()["href"], '{"@type":"BillingCycleSpecification","description":null}'
    )
    assert cleared.status_code == 200, cleared.text
    assert "description" not in cleared.json()
    renamed = api.patch_raw(
        spare.json()["href"],
        '{"@type":"BillingCycleSpecification","name":"Spare 2","description":"x"}',
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Spare 2"
    assert (
        api.patch_raw(
            fmt.json()["href"], '{"@type":"BillFormat","description":"Totals only"}'
        ).status_code
        == 200
    )
    assert (
        api.patch_raw(
            medium.json()["href"], '{"@type":"BillPresentationMedia","name":"Paper post"}'
        ).status_code
        == 200
    )
    # the subscriber Edit form: billStructure with only what changed
    account = apidata.account(api)

    def ref(kind, referred, item_id):
        return json.dumps(
            {"id": item_id, "@type": f"{kind}Ref", "@referredType": referred},
            separators=(",", ":"),
        )

    cycle_ref = ref("BillingCycleSpecification", "BillingCycleSpecification", cycle.json()["id"])
    format_ref = ref("BillFormat", "BillFormat", fmt.json()["id"])
    media_refs = ",".join(
        ref("BillPresentationMedia", "BillPresentationMedia", m.json()["id"])
        for m in (medium, other)
    )
    chosen = api.patch_raw(
        account["href"],
        '{"billStructure":{"cycleSpecification":'
        + cycle_ref
        + ',"format":'
        + format_ref
        + ',"presentationMedia":['
        + media_refs
        + "]}}",
    )
    assert chosen.status_code == 200, chosen.text
    structure = chosen.json()["billStructure"]
    assert structure["cycleSpecification"]["id"] == cycle.json()["id"]
    assert structure["format"]["id"] == fmt.json()["id"]
    assert len(structure["presentationMedia"]) == 2
    # in use: the server refuses the deletes; the UI hides the buttons when it knows
    for item in (cycle, fmt, medium):
        refused = api.delete(item.json()["href"])
        assert refused.status_code == 409, refused.text
    cleared = api.patch_raw(
        account["href"],
        '{"billStructure":{"cycleSpecification":'
        + ref("BillingCycleSpecification", "BillingCycleSpecification", "monthly")
        + ',"format":null,"presentationMedia":[]}}',
    )
    assert cleared.status_code == 200, cleared.text
    structure = cleared.json()["billStructure"]
    assert structure["cycleSpecification"]["id"] == "monthly"
    assert "format" not in structure and "presentationMedia" not in structure
    for item in (cycle, spare, fmt, medium, other):
        assert api.delete(item.json()["href"]).status_code == 204
    assert api.get(cycle.json()["href"]).status_code == 404


def test_payment_and_refund_history_reads_as_the_ui_makes_them(api):
    """api/payments.ts, pinned in Payments.test.tsx: the lists by account.id and status
    (paged by getAll), a payment by id with its paymentItem, and the refunds list."""
    account = apidata.account(api)
    other = apidata.account(api)
    a = account["id"]
    made = api.post(PAYMENT, apidata.payment_body(account, 100))
    assert made.status_code == 201, made.text
    assert api.post(PAYMENT, apidata.payment_body(other, 30)).status_code == 201
    refund = api.post_raw(
        "/tmf-api/payment/v4/refund",
        f'{{"@type":"Refund","account":{{"id":"{a}","@referredType":"BillingAccount"}},'
        '"totalAmount":{"unit":"USD","value":10.00},"paymentMethod":{"name":"bankTransfer"},'
        '"description":"Returned deposit"}',
    )
    assert refund.status_code == 201, refund.text

    page = "offset=0&limit=100"
    mine = api.get(f"{PAYMENT}?account.id={a}&status=done&{page}")
    assert [p["id"] for p in mine.json()] == [made.json()["id"]]
    assert mine.headers["X-Total-Count"] == "1"
    row = mine.json()[0]
    assert row["paymentDate"] and row["status"] == "done"
    assert row["account"]["id"] == a and row["account"]["name"]
    assert row["paymentMethod"]["name"] and row["totalAmount"]["unit"] == "USD"
    assert len(api.get(f"{PAYMENT}?{page}").json()) == 2
    assert api.get(f"{PAYMENT}?account.id={a}&status=pending&{page}").json() == []
    one = api.get(f"{PAYMENT}/{made.json()['id']}")
    assert one.status_code == 200 and one.json()["id"] == made.json()["id"]
    refunds = api.get(f"/tmf-api/payment/v4/refund?account.id={a}&status=done&{page}")
    assert [r["id"] for r in refunds.json()] == [refund.json()["id"]]
    only = refunds.json()[0]
    assert only["refundDate"] and only["status"] == "done"
    assert only["account"]["id"] == a and only["description"] == "Returned deposit"
    assert api.get(f"/tmf-api/payment/v4/refund?{page}").headers["X-Total-Count"] == "1"


def test_product_inventory_reads_and_number_delete_as_the_ui_sends_them(api):
    """api/products.ts and api/numbers.ts of slice 21: the Products list queries and detail,
    the number detail and its delete (pinned in Products.test.tsx and Numbers.test.tsx)."""
    url = "/tmf-api/resourceInventory/v5/resource"

    def number(value):
        created = api.post(
            url,
            {
                "@type": "LogicalResource",
                "name": f"MSISDN {value}",
                "value": value,
                "administrativeState": "unlocked",
                "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    spare, used = number("12025550131"), number("12025550132")
    offering = apidata.sellable_offering(api)
    api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    account = apidata.account(api)
    placed = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
            "requestedStartDate": "2026-10-02T12:00:00+03:00",
            "productOrderItem": [
                {
                    "@type": "ProductOrderItem",
                    "id": "1",
                    "action": "add",
                    "productOffering": {"id": offering["id"], "@type": "ProductOfferingRef"},
                    "product": {
                        "@type": "ProductRefOrValue",
                        "realizingResource": [
                            {
                                "id": used["id"],
                                "@type": "ResourceRef",
                                "@referredType": "LogicalResource",
                            }
                        ],
                    },
                }
            ],
        },
    )
    assert placed.status_code == 201, placed.text
    product = apidata.product_of(api, placed.json())
    page = "offset=0&limit=100"
    everything = api.get(f"{apidata.PRODUCT}?{page}")
    assert everything.status_code == 200 and product["id"] in [p["id"] for p in everything.json()]
    for query in (
        f"status={product['status']}&{page}",
        f"billingAccount.id={account['id']}&{page}",
    ):
        listed = api.get(f"{apidata.PRODUCT}?{query}")
        assert listed.status_code == 200, query
        assert product["id"] in [p["id"] for p in listed.json()]
    assert api.get(f"{apidata.PRODUCT}?status=terminated&{page}").json() == []
    detail = api.get(f"{apidata.PRODUCT}/{product['id']}").json()
    assert detail["productOrderItem"][0]["orderId"] == placed.json()["id"]
    assert detail["realizingResource"][0]["id"] == used["id"]
    assert api.get(f"{url}/{spare['id']}").json()["value"] == "12025550131"
    # a number with an assignment is refused, a never-assigned one deletes (no body)
    refused = api.delete(f"{url}/{used['id']}")
    assert refused.status_code == 409, refused.text
    assert api.delete(f"{url}/{spare['id']}").status_code == 204
    assert api.get(f"{url}/{spare['id']}").status_code == 404


def test_event_subscriptions_as_the_ui_sends_them(api):
    """Integrations.test.tsx: the hub bodies for a v4 (TMF676) and a v5 (TMF620) API, the
    list read, and the delete through the `hub` path the server names."""
    pay = "/tmf-api/payment/v4"
    catalog = "/tmf-api/productCatalogManagement/v5"
    made = api.post_raw(
        f"{pay}/hub",
        '{"callback":"https://example.org/events",'
        '"query":"eventType=RefundCreateEvent,PaymentCreateEvent"}',
    )
    assert made.status_code == 201, made.text
    everything = api.post_raw(f"{catalog}/hub", '{"callback":"https://example.org/all"}')
    assert everything.status_code == 201, everything.text

    listed = api.get("/internal/v1/eventSubscription?offset=0&limit=100")
    assert listed.status_code == 200, listed.text
    by_id = {h["id"]: h for h in listed.json()}
    assert by_id[made.json()["id"]]["eventTypes"] == ["RefundCreateEvent", "PaymentCreateEvent"]
    assert by_id[everything.json()["id"]]["eventTypes"] == []
    for hub in by_id.values():
        assert api.delete(hub["hub"]).status_code == 204
    assert api.get("/internal/v1/eventSubscription?offset=0&limit=100").json() == []


def test_the_ui_event_lists_are_the_servers():
    """frontend/src/api/hubs.ts hard-codes each API's base path and listener events."""
    import re
    from pathlib import Path

    from app.events.types import BASES, EVENT_TYPES

    source = Path("frontend/src/api/hubs.ts").read_text()
    bases = dict(re.findall(r"id: '(TMF\d{3})',[^}]*?base: '([^']+)'", source))
    assert bases == BASES
    block = source.split("export const EVENT_TYPES")[1]
    found = {
        api: set(re.findall(r"'([A-Za-z]+Event)'", body))
        for api, body in re.findall(r"(TMF\d{3}): \[(.*?)\]", block, re.S)
    }
    assert found == {api: set(types) for api, types in EVENT_TYPES.items()}


def test_same_day_status_undo_as_the_ui_sends_it(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    day = api.clock.now().date().isoformat()
    product = apidata.product_of(api, apidata.order(api, account, apidata.add_item(offering)))
    listing = f"/internal/v1/billingAccount/{account['id']}/products"
    for status, restored in [("suspended", "active"), ("active", "suspended")]:
        if status == "active":
            api.post(
                ORDER, ui_product_order(account, ui_status_item(product["id"], "suspended"), day)
            )
            api.clock.set(api.clock.now().replace(day=api.clock.now().day + 1))
            day = api.clock.now().date().isoformat()
        changed = api.post(
            ORDER, ui_product_order(account, ui_status_item(product["id"], status), day)
        )
        assert changed.status_code == 201 and changed.json()["state"] == "completed", changed.text
        row = next(p for p in api.get(listing).json() if p["id"] == product["id"])
        order_id = row["undoableStatusChange"]["productOrder"]["id"]
        result = api.post_raw(
            "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
            '{"@type":"CancelProductOrder","productOrder":{"id":"'
            + order_id
            + '","@type":"ProductOrderRef"}}',
        )
        assert result.status_code == 201, result.text
        assert api.get(product["href"]).json()["status"] == restored
        row = next(p for p in api.get(listing).json() if p["id"] == product["id"])
        assert row["undoableStatusChange"] is None


def test_subscriber_overview_unfinished_order_reads_as_the_ui_makes_them(api):
    """Overview reuses Orders' account/state pages; draft prices use attached catalog rates."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    other = apidata.account(api)
    api.clock.set(api.clock.now().replace(day=2))
    body = {
        "@type": "ProductOrder",
        "billingAccount": {"id": account["id"], "@type": "BillingAccountRef"},
        "requestedStartDate": "2026-10-06T12:00:00+03:00",
        "requestedInitialState": "draft",
        "productOrderItem": [apidata.add_item(offering)],
    }
    draft = api.post(ORDER, body).json()
    assert draft["state"] == "draft"
    for state in ("draft", "acknowledged", "inProgress"):
        page = api.get(f"{ORDER}?billingAccount.id={account['id']}&state={state}&offset=0&limit=50")
        assert page.status_code == 200, page.text
        assert [o["id"] for o in page.json()] == ([draft["id"]] if state == "draft" else [])
        assert (
            api.get(
                f"{ORDER}?billingAccount.id={other['id']}&state={state}&offset=0&limit=50"
            ).json()
            == []
        )
    catalog = api.get(offering["href"]).json()
    for ref in catalog["productOfferingPrice"]:
        rate = api.get(f"{apidata.CATALOG}/productOfferingPrice/{ref['id']}")
        assert rate.status_code == 200 and rate.json()["price"]["unit"] == "USD"


def test_ui_card_requests(api, db_conn):
    """Exact saved-card, card top-up and auto top-up bodies pinned in PaymentMethods.test.tsx."""
    from tests.test_api_topup import prepaid

    methods = "/tmf-api/paymentMethod/v4/paymentMethod"
    _, account = prepaid(api, db_conn)
    base = f"/internal/v1/billingAccount/{account['id']}"
    created = api.post(
        methods,
        {
            "@type": "tokenizedCard",
            "name": "Work",
            "account": [{"id": account["id"], "@referredType": "BillingAccount"}],
            "brand": "visa",
            "lastFourDigits": "4242",
            "expirationDate": "2031-01-01T00:00:00Z",
            "token": "tok_stub_0d3c5a52-1111-4222-8333-444455556666",
        },
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["isPreferred"] is True
    assert "token" not in card
    assert api.patch(f"{methods}/{card['id']}", {"isPreferred": True}).status_code == 200
    topup = api.post_raw(
        f"{base}/cardTopup",
        '{"amount":{"unit":"USD","value":25.00},'
        f'"paymentMethod":{{"id":"{card["id"]}"}},"idempotencyKey":"ui-key-1"}}',
    )
    assert topup.status_code == 202, topup.text
    assert topup.json()["state"] == "pending"
    assert api.get(f"{base}/cardTopup?limit=10").status_code == 200
    rule = api.patch_raw(
        f"{base}/autoTopup",
        '{"enabled":true,"threshold":{"unit":"USD","value":10.00},'
        '"amount":{"unit":"USD","value":20.00}}',
    )
    assert rule.status_code == 200, rule.text
    assert rule.json()["enabled"] is True
    assert api.patch(f"{base}/autoTopup", {"enabled": False}).json()["enabled"] is False
    assert api.delete(f"{methods}/{card['id']}").status_code == 409  # a charge is pending
