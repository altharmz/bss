"""Migration 0003 on the TMF surface: TMF666 BillingAccount.creditLimit and the TMF620
offering code (externalIdentifier). Every response is contract-checked by the client."""

from decimal import Decimal

import pytest

from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, CATALOG, START

OFFERING = f"{CATALOG}/productOffering"


# --- creditLimit (TMF666) ---


def test_credit_limit_is_stored_returned_changed_and_removed(api):
    created = apidata.account(api, creditLimit={"unit": "RUB", "value": 500})
    assert decimals(api.get(created["href"]))["creditLimit"] == {
        "unit": "RUB",
        "value": Decimal("500.00"),
    }
    changed = api.patch(created["href"], {"creditLimit": {"unit": "RUB", "value": 1000.5}})
    assert changed.status_code == 200
    assert decimals(changed)["creditLimit"]["value"] == Decimal("1000.50")
    removed = api.patch(created["href"], {"creditLimit": None})
    assert removed.status_code == 200 and "creditLimit" not in removed.json()


def test_an_account_without_a_credit_limit_has_no_attribute(api):
    assert "creditLimit" not in apidata.account(api)


@pytest.mark.parametrize(
    ("limit", "status"),
    [
        ({"unit": "EUR", "value": 100}, 422),  # not the account's currency
        ({"unit": "RUB", "value": -1}, 422),
        ({"unit": "RUB", "value": 1.005}, 422),  # never rounded
        ({"unit": "RUB"}, 422),
        ({"unit": "RUB", "value": 1, "currency": "RUB"}, 400),
    ],
)
def test_invalid_credit_limits(api, limit, status):
    customer = apidata.customer(api)
    response = api.post(ACCOUNT, apidata.account_body(customer, creditLimit=limit))
    assert response.status_code == status, response.text
    account = apidata.account(api, customer)
    assert api.patch(account["href"], {"creditLimit": limit}).status_code == status


def test_the_credit_limit_never_blocks_an_order(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api, creditLimit={"unit": "RUB", "value": 0})
    api.clock.set(api.clock.now().replace(day=2))
    placed = apidata.order(api, account, apidata.add_item(offering))
    assert placed["state"] == "completed"


# --- offering code (TMF620 externalIdentifier) ---


def offering_body(spec: dict, **over) -> dict:
    return {
        "@type": "ProductOffering",
        "name": "Mobile L",
        "lifecycleStatus": "In Design",
        "validFor": {"startDateTime": START},
        "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
        **over,
    }


@pytest.fixture
def spec(api):
    return api.post(
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Mobile", "lifecycleStatus": "Active"},
    ).json()


def test_code_is_stored_as_external_identifier_and_unique(api, spec):
    response = api.post(
        OFFERING, offering_body(spec, externalIdentifier=apidata.code_identifier("MOB-L"))
    )
    assert response.status_code == 201, response.text
    assert response.json()["externalIdentifier"] == [
        {
            "id": "MOB-L",
            "externalIdentifierType": "offeringCode",
            "owner": "bss",
            "@type": "ExternalIdentifier",
        }
    ]
    again = api.post(
        OFFERING, offering_body(spec, externalIdentifier=apidata.code_identifier("MOB-L"))
    )
    assert again.status_code == 409
    assert again.json()["message"] == "offering code MOB-L is already in use"
    found = api.get(f"{OFFERING}?externalIdentifier.id=MOB-L").json()
    assert [o["id"] for o in found] == [response.json()["id"]]


def test_an_offering_without_a_code_has_no_external_identifier(api, spec):
    created = api.post(OFFERING, offering_body(spec)).json()
    assert "externalIdentifier" not in created


def _identifier(**attributes) -> dict:
    return {"@type": "ExternalIdentifier", **attributes}


CODE = "offeringCode"


@pytest.mark.parametrize(
    ("identifiers", "status"),
    [
        ([_identifier(id="mob-l", externalIdentifierType=CODE)], 422),  # upper case only
        ([_identifier(id="", externalIdentifierType=CODE)], 422),
        ([_identifier(id="A" * 33, externalIdentifierType=CODE)], 422),
        ([_identifier(id="MOB-L", externalIdentifierType=CODE, owner="crm")], 422),
        (apidata.code_identifier("A") + apidata.code_identifier("B"), 422),  # one code only
        ([_identifier(id="MOB-L", externalIdentifierType=CODE, extra=1)], 400),
        ([{"id": "MOB-L", "externalIdentifierType": CODE}], 422),  # the spec requires @type
        ([_identifier(externalIdentifierType=CODE)], 422),  # and id
    ],
)
def test_invalid_codes(api, spec, identifiers, status):
    response = api.post(OFFERING, offering_body(spec, externalIdentifier=identifiers))
    assert response.status_code == status, response.text


def test_other_external_identifiers_are_kept_and_are_no_code(api, spec):
    identifiers = [
        _identifier(id="SKU-1", externalIdentifierType="sku", owner="erp"),
        _identifier(id="lower-case is fine here"),
        *apidata.code_identifier("MOB-L"),
    ]
    created = api.post(OFFERING, offering_body(spec, externalIdentifier=identifiers))
    assert created.status_code == 201, created.text
    assert created.json()["externalIdentifier"] == [
        *identifiers[:2],
        {**identifiers[2], "owner": "bss"},  # the code's owner, filled in
    ]
    found = api.get(f"{OFFERING}?externalIdentifier.id=MOB-L").json()
    assert [o["id"] for o in found] == [created.json()["id"]]
    assert api.get(f"{OFFERING}?externalIdentifier.id=SKU-1").json() == []  # only the code


def test_the_code_changes_with_patch_and_stays_unique(api, spec):
    created = api.post(OFFERING, offering_body(spec)).json()
    other = api.post(
        OFFERING, offering_body(spec, externalIdentifier=apidata.code_identifier("TAKEN"))
    ).json()
    response = api.patch(created["href"], {"externalIdentifier": apidata.code_identifier("NEW")})
    assert response.status_code == 200, response.text
    assert response.json()["externalIdentifier"][0]["id"] == "NEW"
    taken = api.patch(created["href"], {"externalIdentifier": apidata.code_identifier("TAKEN")})
    assert taken.status_code == 409
    assert taken.json()["message"] == "offering code TAKEN is already in use"
    removed = api.patch(other["href"], {"externalIdentifier": None})
    assert removed.status_code == 200 and "externalIdentifier" not in removed.json()
    # the code is free again
    reused = api.patch(created["href"], {"externalIdentifier": apidata.code_identifier("TAKEN")})
    assert reused.status_code == 200, reused.text
