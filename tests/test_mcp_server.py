"""The MCP connector, slice 1 (docs/mcp.md): static bearer tokens, scopes enforced in the
server, rate limit, body limit, money as decimal strings, X-Remote-User, audit, and the
startup identity check. The app behind it is the real one (`api` fixture)."""

import json
import logging
from datetime import date

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from bss_mcp.bss import NotBss
from bss_mcp.clock import FixedClock
from bss_mcp.server import build_app
from bss_mcp.store import Store
from tests import apidata
from tests.apidata import PARTY, PAYMENT
from tests.test_api_bill_settlement import account_with_bills, bills
from tests.test_billing_db import at

INDIVIDUALS = f"{PARTY}/individual"
GIVEN = {"givenName": "Zoya", "familyName": "Kosmodemyanskaya"}


def count(api, url: str) -> int:
    return int(api.get(url).headers["x-total-count"])


# --- authentication ---


@pytest.mark.smoke
def test_no_token_is_a_401_that_points_to_the_resource_metadata(mcp):
    response = mcp.rpc("tools/list", None, token=None)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp"' in (
        challenge
    )


@pytest.mark.parametrize("token", ["nonsense", "bss_st_" + "x" * 43])
def test_an_unknown_token_is_a_401(mcp, token):
    response = mcp.rpc("tools/list", None, token=token)
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_a_revoked_token_is_a_401(mcp):
    record, token = mcp.store.create_static_token("ci", "read")
    assert mcp.rpc("tools/list", None, token).status_code == 200
    assert mcp.store.revoke_static_token(record.id)
    assert mcp.rpc("tools/list", None, token).status_code == 401
    assert not mcp.store.revoke_static_token(record.id)  # once


# --- scopes ---


@pytest.mark.smoke
def test_a_read_token_sees_only_the_read_tools(mcp):
    read = mcp.tools(mcp.token("reader", "read"))
    everything = mcp.tools(mcp.token("writer", "write"))
    assert set(read) == {n for n, t in everything.items() if t["annotations"]["readOnlyHint"]}
    assert "list_customers" in read and "create_individual" not in read
    assert {"create_individual", "create_payment", "patch_bill"} < set(everything)
    assert len(everything) > len(read) > 0
    assert everything["list_customers"]["annotations"]["readOnlyHint"] is True
    assert everything["create_payment"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": False,
    }


def test_a_read_token_calling_a_write_tool_is_a_403_and_nothing_is_applied(api, mcp, caplog):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    before = count(api, INDIVIDUALS)
    response = mcp.call(mcp.token("reader", "read"), "create_individual", {"body": GIVEN})
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge and 'scope="read write"' in challenge
    assert count(api, INDIVIDUALS) == before
    [line] = [json.loads(r.getMessage()) for r in caplog.records]
    assert line["outcome"] == 403 and line["principal"] == "mcp:reader"


def test_a_write_token_writes_and_reads(api, mcp):
    token = mcp.token("writer", "write")
    is_error, answer = mcp.result(token, "create_individual", {"body": GIVEN})
    assert not is_error and answer["status"] == 201
    assert answer["body"]["givenName"] == "Zoya"
    apidata.customer(api, answer["body"])
    is_error, listing = mcp.result(mcp.token("reader", "read"), "list_customers", {"limit": 5})
    assert not is_error and listing["status"] == 200 and listing["totalCount"] == 1


def test_the_app_refusing_a_call_is_a_tool_error_not_an_http_one(mcp):
    is_error, answer = mcp.result(
        mcp.token("writer", "write"), "patch_bill", {"bill_id": "nope", "body": {"state": "onHold"}}
    )
    assert is_error and answer["status"] == 404


# --- arguments ---


def test_arguments_are_checked_against_the_schema_before_the_app_is_called(api, mcp):
    before = count(api, INDIVIDUALS)
    token = mcp.token("writer", "write")
    for arguments in ({}, {"body": {**GIVEN, "shoeSize": "42"}}, {"body": GIVEN, "x": 1}):
        is_error, text = mcp.result(token, "create_individual", arguments)
        assert is_error and text.startswith("invalid arguments"), text
    assert count(api, INDIVIDUALS) == before


def payment_arguments(api, value) -> dict:
    body = apidata.payment_body(apidata.account(api), value)
    return {"body": json.loads(json.dumps(body, default=str))}


@pytest.mark.parametrize("value", [10.5, 10.0])
def test_a_float_amount_is_refused_and_nothing_is_stored(api, mcp, value):
    arguments = payment_arguments(api, "0")
    arguments["body"]["totalAmount"]["value"] = value
    before = count(api, PAYMENT)
    is_error, text = mcp.result(mcp.token("writer", "write"), "create_payment", arguments)
    assert is_error and text.startswith("body.totalAmount.value: a JSON number"), text
    assert count(api, PAYMENT) == before


@pytest.mark.parametrize("value", [10, "10.123", "1e3", "ten"])
def test_an_amount_that_is_not_a_decimal_string_is_refused(api, mcp, value):
    arguments = payment_arguments(api, "0")
    arguments["body"]["totalAmount"]["value"] = value
    is_error, text = mcp.result(mcp.token("writer", "write"), "create_payment", arguments)
    assert is_error and "body/totalAmount/value" in text, text
    assert count(api, PAYMENT) == 0


def test_a_decimal_string_reaches_the_app_as_an_exact_amount(api, mcp):
    arguments = payment_arguments(api, "0")
    arguments["body"]["totalAmount"]["value"] = "1234.56"
    is_error, answer = mcp.result(mcp.token("writer", "write"), "create_payment", arguments)
    assert not is_error and answer["status"] == 201, answer
    assert answer["body"]["totalAmount"] == {"unit": "USD", "value": 1234.56}
    stored = api.get(answer["body"]["href"]).text
    assert '"value":1234.56' in stored.replace(" ", "")


def test_amounts_in_the_schema_are_decimal_strings(mcp):
    tool = mcp.tools(mcp.token("writer", "write"))["create_payment"]
    money = tool["inputSchema"]["properties"]["body"]["properties"]["totalAmount"]
    assert money["properties"]["value"]["type"] == "string"
    assert '"number"' not in json.dumps(tool["inputSchema"])


# --- X-Remote-User ---


def test_the_app_records_the_principal_as_the_operator(api, db_conn, mcp):
    account = account_with_bills(
        api, db_conn, apidata.sellable_offering(api, value=300), date(2026, 12, 1)
    )
    [b] = bills(api, account)
    api.clock.set(at(date(2026, 12, 3)))
    is_error, answer = mcp.result(
        mcp.token("ops-bot", "write"),
        "patch_bill",
        {"bill_id": b["id"], "body": {"state": "onHold"}},
    )
    assert not is_error and answer["body"]["state"] == "onHold", answer
    history = api.get(f"/internal/v1/customerBill/{b['id']}").json()["history"]
    assert history[-1]["toState"] == "onHold"
    assert history[-1]["operator"] == "mcp:ops-bot"


# --- limits ---


def test_the_rate_limit_is_per_principal_and_refills(mcp_settings, api, request):
    mcp_settings.mcp_rate_per_min = 3
    mcp = request.getfixturevalue("mcp")
    alice, bob = mcp.token("alice", "read"), mcp.token("bob", "read")
    for _ in range(3):
        assert mcp.rpc("tools/list", None, alice).status_code == 200
    limited = mcp.rpc("tools/list", None, alice)
    assert limited.status_code == 429 and limited.headers["retry-after"] == "20"
    assert mcp.rpc("tools/list", None, bob).status_code == 200  # someone else's bucket
    mcp.clock.advance(20)
    assert mcp.rpc("tools/list", None, alice).status_code == 200
    assert mcp.rpc("tools/list", None, alice).status_code == 429


def test_a_body_above_the_limit_is_a_413(mcp):
    token = mcp.token("writer", "write")
    big = {"body": {**GIVEN, "familyName": "x" * 70_000}}
    response = mcp.call(token, "create_individual", big)
    assert response.status_code == 413


# --- audit ---


def test_the_audit_log_has_one_line_per_call_and_no_secrets(api, mcp, caplog):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    token = mcp.token("writer", "write")
    mcp.result(token, "create_individual", {"body": GIVEN})
    mcp.result(token, "create_payment", payment_arguments(api, 10.5))
    mcp.rpc("tools/list", None, token)  # not a tool call: no line
    lines = [json.loads(r.getMessage()) for r in caplog.records]
    assert [(line["tool"], line["outcome"]) for line in lines] == [
        ("create_individual", 201),
        ("create_payment", "invalid_arguments"),
    ]
    assert all(line["principal"] == "mcp:writer" and line["time"].endswith("Z") for line in lines)
    text = caplog.text
    assert token not in text and "Zoya" not in text and "10.5" not in text


# --- the startup identity check ---


def _app_answering(body: dict, status: int = 200) -> Starlette:
    return Starlette(routes=[Route("/", lambda request: JSONResponse(body, status_code=status))])


def _start(settings, transport) -> None:
    clock = FixedClock()
    connector = build_app(
        settings, clock=clock, store=Store(settings.mcp_store, clock), transport=transport
    )
    with TestClient(connector):
        pass


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"service": "other", "status": "ok"}, 200),
        ({"service": "bss", "status": "ok", "extra": 1}, 200),
        ({"service": "bss", "status": "ok"}, 500),
    ],
)
def test_the_connector_refuses_to_start_unless_it_talks_to_bss(mcp_settings, body, status):
    with pytest.raises(NotBss, match="is not bss"):
        _start(mcp_settings, httpx2.ASGITransport(app=_app_answering(body, status)))


def test_the_connector_refuses_to_start_when_bss_never_answers(mcp_settings):
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(NotBss, match="did not answer"):
        _start(mcp_settings, httpx2.MockTransport(refuse))


@pytest.mark.smoke
def test_the_connector_starts_against_bss(mcp_settings):
    _start(
        mcp_settings, httpx2.ASGITransport(app=_app_answering({"service": "bss", "status": "ok"}))
    )


# --- the store ---


def test_static_tokens_are_stored_as_hashes_only(mcp_settings):
    store = Store(mcp_settings.mcp_store, FixedClock())
    record, token = store.create_static_token("ci", "write")
    assert token.startswith("bss_st_") and len(token) > 40
    with open(mcp_settings.mcp_store, "rb") as f:
        assert token.encode() not in f.read()
    assert store.find_static_token(token) == record
    assert [t.name for t in store.static_tokens()] == ["ci"]


@pytest.mark.parametrize(
    ("name", "scope"), [("ci", "admin"), ("Bad Name", "read"), ("a:b", "read")]
)
def test_a_token_needs_a_plain_name_and_a_known_scope(mcp_settings, name, scope):
    store = Store(mcp_settings.mcp_store, FixedClock())
    with pytest.raises(ValueError):
        store.create_static_token(name, scope)


def test_token_names_are_unique(mcp_settings):
    store = Store(mcp_settings.mcp_store, FixedClock())
    store.create_static_token("ci", "read")
    with pytest.raises(ValueError, match="already exists"):
        store.create_static_token("ci", "write")


@pytest.mark.smoke
def test_healthz(mcp):
    assert mcp.client.get("/healthz").json() == {"status": "ok"}
