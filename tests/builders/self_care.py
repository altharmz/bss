"""Shared builders of tests/test_self_care.py, used by other test modules (#423)."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.integrations.notify import adapter as sender
from app.main import app
from tests import apidata
from tests.apiclient import TmfClient
from tests.builders.prepaid_renewal_producer import tick

BASE = "/self-care/v1"

PASSWORD = "a-long-customer-password"

EMAIL = "ada@example.org"


def customer_of(db_conn, acc):
    (customer_id,) = db_conn.execute(
        "SELECT customer_id FROM billing_account WHERE id = %s", (acc["id"],)
    ).fetchone()
    db_conn.commit()
    return customer_id


def signed_in(api, care, db_conn, acc, email=EMAIL):
    """The account's customer, invited and signed in on `care`; the login's id."""
    _, login, code = invite(api, email, customer={"id": str(customer_of(db_conn, acc))})
    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204
    assert sign_in(care, email).status_code == 200
    (login_id,) = db_conn.execute(
        "SELECT id FROM customer_login WHERE email = %s", (email,)
    ).fetchone()
    db_conn.commit()
    return login_id


def settle(db_conn, api):
    """Two wake-ups: the charge is sent, then the gateway's answer is read."""
    tick(db_conn, api.clock.now())
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    tick(db_conn, api.clock.now())


class CareClient(TmfClient):
    """A customer's browser: no operator cookie, no re-sign-in on a 401; every response is
    still checked against the self-care contract."""

    def request(self, method, url, **kwargs):
        response = self.client.request(method, url, **kwargs)
        self._check(method, url.split("?")[0], response)
        return response


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "self_care_enabled", True)


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


@pytest.fixture
def care(api):
    with TestClient(app, headers={"X-Bss-Request": "1"}, raise_server_exceptions=False) as raw:
        yield CareClient(raw)


def new_care(api):
    return CareClient(
        TestClient(app, headers={"X-Bss-Request": "1"}, raise_server_exceptions=False)
    )


def login_url(customer_id):
    return f"/internal/v1/customer/{customer_id}/careLogin"


def last_code(kind):
    """The code in the last email of the kind ('sign-in' or a link code)."""
    body = sender().sent[-1].body
    if kind == "sign-in":
        return re.search(r"code is (\d{6})\.", body).group(1)
    return re.search(r": ([A-Za-z0-9_-]{20,})", body).group(1)


def invite(api, email=EMAIL, customer=None):
    customer = customer or apidata.customer(api)
    response = api.post(login_url(customer["id"]), {"email": email})
    assert response.status_code == 201, response.text
    return customer, response.json(), last_code("link")


def sign_in(care, email=EMAIL, password=PASSWORD):
    first = care.post(BASE + "/session", {"email": email, "password": password})
    assert first.status_code == 202, first.text
    return care.post(BASE + "/session/verify", {"email": email, "code": last_code("sign-in")})
