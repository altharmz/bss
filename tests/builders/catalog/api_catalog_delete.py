"""Builders other test modules share, from tests/catalog/test_api_catalog_delete.py (#423)."""

import threading


def gone(api, href: str) -> None:
    assert api.get(href).status_code == 404
    assert api.delete(href).status_code == 404


def refused(api, href: str, *expected: str) -> str:
    response = api.delete(href)
    assert response.status_code == 409, response.text
    message = response.json()["message"]
    for text in expected:
        assert text in message, message
    assert api.get(href).status_code == 200
    return message


def _waits(target) -> dict:
    """Start `target` on a thread; it must still be blocked two seconds later."""
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(response=target()))
    thread.start()
    thread.join(timeout=2)
    assert thread.is_alive(), "it did not wait for the open transaction"
    result["thread"] = thread
    return result


def _finish(result: dict):
    result["thread"].join(timeout=10)
    assert not result["thread"].is_alive()
    return result["response"]
