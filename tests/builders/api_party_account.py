"""Shared builders of tests/test_api_party_account.py, used by other test modules (#423)."""

from tests.builders.api_financial_account import owner

BASE = "/tmf-api/accountManagement/v5"


def create(api, resource, party, **over):
    body = {"name": "Partner payouts", "relatedParty": [owner(party)], **over}
    response = api.post(f"{BASE}/{resource}", body)
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()
