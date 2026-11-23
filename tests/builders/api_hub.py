"""Shared builders of tests/test_api_hub.py, used by other test modules (#423)."""

from app.events.types import BASES

CALLBACK = "http://listener.test/events"


def hub(api, name: str, **body) -> dict:
    response = api.post(f"{BASES[name]}/hub", {"callback": CALLBACK, **body})
    assert response.status_code == 201, response.text
    return response.json()
