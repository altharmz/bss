"""Builders other test modules share, from tests/resources/test_api_resource_inventory.py (#423)."""

BASE = "/tmf-api/resourceInventory/v5"

URL = BASE + "/resource"


def number(api, value="12025550123", **extra):
    response = api.post(
        URL, {"@type": "LogicalResource", "name": "Number", "value": value, **extra}
    )
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()
