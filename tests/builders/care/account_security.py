"""Builders other test modules share, from tests/care/test_account_security.py (#423)."""

SEC = "/internal/v1/security"

PIN = "482913"


def head(pin):
    return {} if pin is None else {"X-Account-Pin": pin}


def set_pin(api, customer, pin=PIN, reason="identity checked at the counter"):
    return api.put(f"{SEC}/pin/{customer}", {"pin": pin, "reason": reason})


def audit(api, **filters):
    query = "&".join(f"{k}={v}" for k, v in filters.items())
    response = api.get(f"{SEC}/audit?limit=100" + (f"&{query}" if query else ""))
    assert response.status_code == 200, response.text
    return response.json()
