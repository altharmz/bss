"""Builders other test modules share, from tests/billing/test_tax_engine.py (#423)."""

from tests.apidata import ACCOUNT, service_address_contact

CA = {
    "street1": "1 Market St",
    "city": "San Francisco",
    "stateOrProvince": "CA",
    "postCode": "94105",
}


def set_address(api, acc, address, **kw):
    return api.patch(f"{ACCOUNT}/{acc['id']}", {"contact": service_address_contact(address)}, **kw)
