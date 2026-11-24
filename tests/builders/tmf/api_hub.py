"""Builders other test modules share, from tests/tmf/test_api_hub.py (#423)."""

from app.events.types import BASES

CALLBACK = "http://listener.test/events"


def hub(api, name: str, **body) -> dict:
    response = api.post(f"{BASES[name]}/hub", {"callback": CALLBACK, **body})
    assert response.status_code == 201, response.text
    return response.json()
