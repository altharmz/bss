"""The System screens' endpoints are admin-only on the server (#467, docs/decisions/users.md U4):
a `user` session gets 403, an admin and the MCP service principal do not."""
# ruff: noqa: F811

import pytest

from app.api.hub import HUB_APIS
from app.events.types import BASES
from tests.builders.internal.users import (  # noqa: F401 - fixtures
    activate,
    client,
    create,
    login,
)

ACCOUNT = "/tmf-api/accountManagement/v5"
ZERO = "00000000-0000-0000-0000-000000000000"
MCP = {"X-Bss-Service": "test-service-credential", "X-Remote-User": "mcp-token:demo"}

GATED = [
    ("POST", f"{ACCOUNT}/billingCycleSpecification"),
    ("PATCH", f"{ACCOUNT}/billingCycleSpecification/{ZERO}"),
    ("DELETE", f"{ACCOUNT}/billingCycleSpecification/{ZERO}"),
    ("POST", f"{ACCOUNT}/billFormat"),
    ("PATCH", f"{ACCOUNT}/billFormat/{ZERO}"),
    ("DELETE", f"{ACCOUNT}/billFormat/{ZERO}"),
    ("POST", f"{ACCOUNT}/billPresentationMedia"),
    ("PATCH", f"{ACCOUNT}/billPresentationMedia/{ZERO}"),
    ("DELETE", f"{ACCOUNT}/billPresentationMedia/{ZERO}"),
    ("GET", "/internal/v1/lifecycle/settings"),
    ("PUT", "/internal/v1/lifecycle/settings"),
    ("GET", "/internal/v1/dunning/settings"),
    ("PATCH", "/internal/v1/dunning/settings"),
    ("GET", "/internal/v1/eventSubscription"),
    ("GET", "/internal/v1/wholesale/invoice"),
    ("POST", "/internal/v1/wholesale/invoice"),
    ("GET", f"/internal/v1/wholesale/invoice/{ZERO}"),
    ("GET", f"/internal/v1/wholesale/invoice/{ZERO}/discrepancies"),
    ("GET", f"/internal/v1/wholesale/invoice/{ZERO}/lines"),
    *[("POST", f"{BASES[api]}/hub") for api, _ in HUB_APIS],
    *[("DELETE", f"{BASES[api]}/hub/{ZERO}") for api, _ in HUB_APIS],
    *[("GET", f"{BASES[api]}/hub/{ZERO}") for api in ("TMF639", "TMF629", "TMF621")],
]

# the account Edit form selects these: every operator reads them
OPEN_READS = [
    f"{ACCOUNT}/billingCycleSpecification",
    f"{ACCOUNT}/billFormat",
    f"{ACCOUNT}/billPresentationMedia",
    "/internal/v1/dunning/accounts",
]


def call(client, method, path, **kwargs):
    body = {} if method in {"POST", "PUT", "PATCH"} else None
    return client.request(method, path, json=body, **kwargs)


@pytest.fixture
def as_user(client):
    assert login(client).status_code == 200
    create(client)
    activate(client)
    return client


def test_a_user_is_refused_every_system_endpoint(as_user):
    for method, path in GATED:
        response = call(as_user, method, path)
        assert response.status_code == 403, (method, path, response.text)
        assert "administrator role required" in response.text


def test_a_user_still_reads_what_the_account_form_and_dunning_screen_need(as_user):
    for path in OPEN_READS:
        assert as_user.get(path).status_code == 200, path


def test_an_admin_and_the_mcp_service_pass_the_gate(client):
    assert login(client).status_code == 200
    for method, path in GATED:
        admin = call(client, method, path)
        assert admin.status_code != 403, (method, path, admin.text)
    client.cookies.clear()
    for method, path in GATED:
        service = call(client, method, path, headers=MCP)
        assert service.status_code != 403, (method, path, service.text)
