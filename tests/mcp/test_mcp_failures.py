"""The MCP connector against a stubbed bss (#485): what a tool call and the login page
answer when the app times out, cannot be reached or answers something unexpected, and
that nothing is retried. No database: the upstream is an httpx MockTransport that records
every request. The small units the HTTP tests cannot reach are at the end."""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from mcp.server.auth.provider import RefreshToken, TokenError
from prometheus_client import generate_latest
from starlette.authentication import UnauthenticatedUser
from starlette.testclient import TestClient

from bss_mcp import metrics, passwords, schema
from bss_mcp.auth import caller
from bss_mcp.bss import IDENTITY, NotBss
from bss_mcp.clock import Clock, FixedClock
from bss_mcp.oauth import MAX_CLIENT_NAME, MAX_REDIRECT_URIS, Provider
from bss_mcp.routes import PublicClientRevoke
from bss_mcp.server import BodyLimit, _replay, build_app
from bss_mcp.store import Store
from bss_mcp.tools import DriftError, Tool, list_filters
from tests.builders.mcp.mcp_oauth import client_id, login, login_link, pkce, register
from tests.mcpclient import Mcp

REPO = Path(__file__).resolve().parents[2]
CHECK = "/internal/v1/service/credential-check"
LOG_LEVEL = "/internal/v1/service/log-level"
VERIFIED = {"username": "demo", "role": "mcp", "credentialVersion": 1}

TOOLS = {
    t.name: t
    for t in (
        Tool("get_thing", "GET", "/thing/{thing_id}", "Read a thing."),
        Tool("delete_thing", "DELETE", "/thing/{thing_id}", "Delete a thing."),
    )
}


class Upstream:
    """bss as the connector sees it: `GET /` is bss; every other request is recorded
    and answered by `answer` (which may raise a transport error)."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.answer = lambda request: httpx2.Response(200, json={"id": "t1"})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/":
            return httpx2.Response(200, json=IDENTITY)
        self.requests.append(request)
        return self.answer(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def raising(exc_type: type[httpx2.TransportError]):
    def answer(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("stubbed", request=request)

    return answer


def answering(status: int, **kw):
    return lambda request: httpx2.Response(status, **kw)


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def stubbed(mcp_settings, upstream):
    """The connector with two stub tools in front of `upstream`."""
    clock = FixedClock()
    store = Store(mcp_settings.mcp_store, clock)
    connector = build_app(
        mcp_settings,
        clock=clock,
        store=store,
        transport=httpx2.MockTransport(upstream),
        tools=TOOLS,
    )
    with TestClient(connector, base_url="https://mcp.test") as client:
        yield Mcp(client, store, clock)


def audit_lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "bss_mcp.audit"]


def scrape(mcp_settings, stubbed) -> str:
    mcp_settings.bss_metrics_token = "scrape"
    return stubbed.client.get("/metrics", headers={"Authorization": "Bearer scrape"}).text


def metric(text: str, name: str, **labels) -> float:
    for line in text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


# --- a tool call when bss fails ---


@pytest.mark.parametrize(("tool", "scope"), [("get_thing", "read"), ("delete_thing", "write")])
@pytest.mark.parametrize(
    ("error", "text", "outcome", "result"),
    [
        (httpx2.ReadTimeout, "bss did not answer within 10 s", "timeout", "timeout"),
        (httpx2.ConnectError, "bss is not reachable", "unreachable", "server_error"),
    ],
)
def test_a_failed_call_to_bss_is_a_tool_error_and_is_not_retried(
    stubbed, upstream, mcp_settings, caplog, tool, scope, error, text, outcome, result
):
    caplog.set_level(logging.INFO, logger="bss_mcp")
    before = scrape(mcp_settings, stubbed)
    upstream.answer = raising(error)
    is_error, answer = stubbed.result(stubbed.token("bot", scope), tool, {"thing_id": "t1"})
    assert is_error and answer == text
    assert upstream.paths() == ["/thing/t1"]  # one request: no retry, a write neither
    [line] = audit_lines(caplog)
    assert (line["tool"], line["outcome"]) == (tool, outcome)
    labels = {"tool": tool, "outcome": result}
    after = scrape(mcp_settings, stubbed)
    assert metric(after, "bss_mcp_tool_calls_total", **labels) == (
        metric(before, "bss_mcp_tool_calls_total", **labels) + 1
    )


def test_an_unreachable_bss_is_logged_without_the_token(stubbed, upstream, caplog):
    caplog.set_level(logging.WARNING, logger="bss_mcp")
    upstream.answer = raising(httpx2.ConnectError)
    token = stubbed.token("bot", "read")
    stubbed.result(token, "get_thing", {"thing_id": "t1"})
    [warning] = [r for r in caplog.records if r.name == "bss_mcp"]
    assert warning.getMessage().startswith("call to bss failed: ConnectError(")
    assert token not in caplog.text


def test_a_body_that_is_not_json_is_passed_on_as_text(stubbed, upstream):
    upstream.answer = answering(502, content=b"<html>bad gateway</html>")
    is_error, answer = stubbed.result(stubbed.token("bot", "read"), "get_thing", {"thing_id": "1"})
    assert is_error and answer == {"status": 502, "body": "<html>bad gateway</html>"}
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("content", [b"unauthorized", b'["credentialRevoked"]'])
def test_a_401_that_is_not_credential_revoked_keeps_the_grant(stubbed, upstream, content):
    upstream.answer = answering(401, content=content)
    token = stubbed.token("bot", "read")
    is_error, answer = stubbed.result(token, "get_thing", {"thing_id": "1"})
    assert is_error and answer["status"] == 401
    # not the revoked-credential answer: the HTTP status stays 200 and the token works
    upstream.answer = answering(200, json={"id": "1"})
    assert stubbed.result(token, "get_thing", {"thing_id": "1"}) == (
        False,
        {"status": 200, "body": {"id": "1"}},
    )


def test_bss_rate_limiting_a_call_is_counted_as_rate_limited(stubbed, upstream, mcp_settings):
    before = scrape(mcp_settings, stubbed)
    upstream.answer = answering(429, json={"code": "tooManyRequests"})
    is_error, answer = stubbed.result(stubbed.token("bot", "read"), "get_thing", {"thing_id": "1"})
    assert is_error and answer["status"] == 429
    labels = {"tool": "get_thing", "outcome": "rate_limited"}
    assert metric(scrape(mcp_settings, stubbed), "bss_mcp_tool_calls_total", **labels) == (
        metric(before, "bss_mcp_tool_calls_total", **labels) + 1
    )


# --- requests the tools never see ---


def test_an_unknown_tool_is_a_tool_error_without_a_call_to_bss(
    stubbed, upstream, mcp_settings, caplog
):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    before = scrape(mcp_settings, stubbed)
    is_error, text = stubbed.result(stubbed.token("bot", "write"), "drop_everything", {})
    assert is_error and text == "no tool named drop_everything"
    assert upstream.requests == []
    [line] = audit_lines(caplog)
    assert line["outcome"] == "unknown_tool"
    labels = {"tool": "unknown", "outcome": "client_error"}  # the name is never a label
    after = scrape(mcp_settings, stubbed)
    assert metric(after, "bss_mcp_tool_calls_total", **labels) == (
        metric(before, "bss_mcp_tool_calls_total", **labels) + 1
    )
    assert "drop_everything" not in after


def test_malformed_json_is_a_json_rpc_parse_error_without_a_call_to_bss(stubbed, upstream):
    response = stubbed.client.post(
        "/mcp",
        content=b'{"jsonrpc": "2.0", "method": "tools/call"',
        headers={
            "Authorization": f"Bearer {stubbed.token('bot', 'write')}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700
    assert upstream.requests == []


def test_a_batched_write_call_with_a_read_token_does_not_reach_bss(stubbed, upstream):
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    call["params"] = {"name": "delete_thing", "arguments": {"thing_id": "t1"}}
    response = stubbed.post([call], stubbed.token("bot", "read"))
    # Guard sees no single tools/call; the SDK refuses a batch (MCP has none since
    # 2025-06-18), so the handler's own scope check stays unreachable (server.py)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
    assert upstream.requests == []


# --- the connector's own health and the log level poll ---


def test_healthz_is_degraded_when_the_store_fails(stubbed, monkeypatch, caplog):
    def broken() -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(stubbed.store, "ping", broken)
    response = stubbed.client.get("/healthz")
    assert (response.status_code, response.json()) == (503, {"status": "degraded"})
    assert "healthz: store unreachable: disk I/O error" in caplog.text


def test_a_failed_log_level_poll_keeps_the_level_and_polls_again(mcp_settings, upstream, caplog):
    caplog.set_level(logging.WARNING, logger="app.logsetup")
    mcp_settings.log_poll_seconds = 1
    polled = threading.Semaphore(0)

    def answer(request: httpx2.Request) -> httpx2.Response:
        polled.release()
        return httpx2.Response(500, json={"code": "internal"})

    upstream.answer = answer
    level = logging.getLogger("bss_mcp").level
    clock = FixedClock()
    connector = build_app(
        mcp_settings,
        clock=clock,
        store=Store(mcp_settings.mcp_store, clock),
        transport=httpx2.MockTransport(upstream),
        tools=TOOLS,
    )
    with TestClient(connector):
        assert polled.acquire(timeout=10) and polled.acquire(timeout=10)  # the loop goes on
    assert set(upstream.paths()) == {LOG_LEVEL}
    unreadable = [r.getMessage() for r in caplog.records if r.name == "app.logsetup"]
    keeping = mcp_settings.log_level.upper()
    assert unreadable == [f"log level setting unreadable (HTTPStatusError); keeping {keeping}"]
    assert logging.getLogger("bss_mcp").level == level


# --- the startup identity check ---


def _start(settings, handler) -> None:
    clock = FixedClock()
    connector = build_app(
        settings,
        clock=clock,
        store=Store(settings.mcp_store, clock),
        transport=httpx2.MockTransport(handler),
        tools=TOOLS,
    )
    with TestClient(connector):
        pass


def test_the_identity_check_retries_a_bss_that_is_still_starting(mcp_settings, caplog):
    caplog.set_level(logging.INFO, logger="bss_mcp")
    mcp_settings.mcp_identity_wait = 30
    asked: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        asked.append(request.url.path)
        if len(asked) == 1:
            raise httpx2.ConnectError("refused", request=request)
        return httpx2.Response(200, json=IDENTITY)

    _start(mcp_settings, handler)
    assert asked == ["/", "/"]
    assert "not answering yet, retrying" in caplog.text


def test_an_identity_answer_that_is_not_json_is_not_bss(mcp_settings):
    with pytest.raises(NotBss, match="is not bss: GET / answered 200 '<html>"):
        _start(mcp_settings, lambda request: httpx2.Response(200, content=b"<html>hi</html>"))


# --- the login page when the credential check fails ---


@pytest.fixture
def link(stubbed):
    """A parked /authorize request: the login page's `request` value."""
    _, challenge = pkce()
    return login_link(stubbed, client_id(stubbed), challenge)


@pytest.mark.parametrize(
    ("answer", "status", "message", "outcome", "result"),
    [
        (raising(httpx2.ConnectError), 503, "bss is not reachable.", "bss down", "error"),
        (raising(httpx2.ReadTimeout), 503, "bss is not reachable.", "bss down", "error"),
        (answering(500, json={}), 503, "bss could not check this.", "bss error", "error"),
        (answering(429, json={}), 429, "Too many attempts. Wait a minute.", 429, "rate_limited"),
    ],
)
def test_a_failed_credential_check_shows_why_and_keeps_the_link(
    stubbed, upstream, link, mcp_settings, caplog, answer, status, message, outcome, result
):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    before = scrape(mcp_settings, stubbed)
    upstream.answer = answer
    response = login(stubbed, link)
    assert response.status_code == status and message in response.text
    assert "location" not in response.headers  # no code, no redirect
    assert upstream.paths() == [CHECK]  # asked once, not retried
    [line] = [x for x in audit_lines(caplog) if x["event"] == "login"]
    assert (line["principal"], line["outcome"]) == ("mcp:demo", outcome)
    labels = {"event": "login", "result": result}
    assert metric(scrape(mcp_settings, stubbed), "bss_mcp_oauth_events_total", **labels) == (
        metric(before, "bss_mcp_oauth_events_total", **labels) + 1
    )
    # bss's trouble is not a wrong password: the same link still signs in
    upstream.answer = answering(200, json=VERIFIED)
    approved = login(stubbed, link)
    assert approved.status_code == 302 and "code=" in approved.headers["location"]


def test_failed_logins_are_limited_per_user_across_ips(mcp_settings, upstream, request):
    mcp_settings.mcp_auth_rate_per_min = 3  # one for the registration
    mcp_settings.mcp_trust_forwarded = True
    stubbed = request.getfixturevalue("stubbed")
    _, challenge = pkce()
    request_id = login_link(stubbed, client_id(stubbed), challenge)
    upstream.answer = answering(401, json={"code": "invalidCredentials"})

    def attempt(ip: str):
        form = {"request": request_id, "user": "demo", "password": "nope", "action": "approve"}
        return stubbed.client.post("/login", data=form, headers={"X-Forwarded-For": ip})

    assert [attempt(f"203.0.113.{i}").status_code for i in range(4)] == [401, 401, 401, 429]
    assert "Too many attempts. Wait a minute." in attempt("203.0.113.9").text
    assert len(upstream.requests) == 3  # the limited attempts never reach bss


def test_a_link_used_up_during_the_credential_check_issues_no_code(stubbed, upstream, link):
    def check(request: httpx2.Request) -> httpx2.Response:
        assert stubbed.store.drop_pending(link)  # a parallel request approved first
        return httpx2.Response(200, json=VERIFIED)

    upstream.answer = check
    response = login(stubbed, link)
    assert response.status_code == 400 and "no longer valid" in response.text
    assert "location" not in response.headers


def test_a_link_whose_client_is_gone_is_no_longer_valid(stubbed, link, mcp_settings):
    db = sqlite3.connect(mcp_settings.mcp_store)
    db.execute("DELETE FROM oauth_client")
    db.commit()
    db.close()
    response = stubbed.client.get("/login", params={"request": link})
    assert response.status_code == 400 and "no longer valid" in response.text


# --- registration and the token endpoint's edges ---


@pytest.mark.parametrize(
    ("over", "error"),
    [
        (
            {"redirect_uris": [f"https://c.example/{i}" for i in range(MAX_REDIRECT_URIS + 1)]},
            "invalid_redirect_uri",
        ),
        ({"client_name": "x" * (MAX_CLIENT_NAME + 1)}, "invalid_client_metadata"),
    ],
)
def test_registration_refuses_metadata_outside_the_limits(stubbed, caplog, over, error):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    response = register(stubbed, **over)
    assert response.status_code == 400 and response.json()["error"] == error
    assert stubbed.store.count_clients() == 0
    assert [x["outcome"] for x in audit_lines(caplog)] == [f"refused: {error}"]


def _provider(mcp_settings) -> Provider:
    clock = FixedClock()
    return Provider(Store(mcp_settings.mcp_store, clock), clock, mcp_settings)


def _oauth_events(**labels) -> float:
    return metric(
        generate_latest(metrics.REGISTRY).decode(), "bss_mcp_oauth_events_total", **labels
    )


def test_a_token_revoked_by_a_parallel_request_is_already_revoked(mcp_settings, caplog):
    """The SDK loads the token, then a parallel /revoke spends it before this one does."""
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    provider = _provider(mcp_settings)
    provider.store.save_token(
        "bss_at_twice", kind="access", grant_id="g", client_id="c", subject="mcp:demo",
        scopes=["read"], resource=None, credential_version=1, ttl=60,
    )  # fmt: skip
    loaded = asyncio.run(provider.load_access_token("bss_at_twice"))
    before = _oauth_events(event="revoke", result="already_revoked")
    asyncio.run(provider.revoke_token(loaded))
    asyncio.run(provider.revoke_token(loaded))
    assert [x["outcome"] for x in audit_lines(caplog)] == ["access revoked", "already revoked"]
    assert _oauth_events(event="revoke", result="already_revoked") == before + 1
    assert provider.store.token("bss_at_twice", "access") is None


def test_a_refresh_token_revoked_after_loading_issues_nothing(mcp_settings):
    provider = _provider(mcp_settings)
    provider.store.save_token(
        "bss_rt_raced", kind="refresh", grant_id="g", client_id="c", subject="mcp:demo",
        scopes=["read"], resource=None, credential_version=1, ttl=60,
    )  # fmt: skip
    loaded = RefreshToken(token="bss_rt_raced", client_id="c", scopes=["read"])
    loaded.subject = "mcp:demo"
    provider.store.revoke_token("bss_rt_raced")  # a parallel refresh spent it
    client = type("Client", (), {"client_id": "c"})()
    with pytest.raises(TokenError) as refused:
        asyncio.run(provider.exchange_refresh_token(client, loaded, ["read"]))
    assert refused.value.error == "invalid_grant"
    assert provider.store.grants() == []


def test_the_identity_assertion_grant_is_not_supported(mcp_settings):
    with pytest.raises(TokenError) as refused:
        asyncio.run(_provider(mcp_settings).exchange_identity_assertion(None, None))
    assert refused.value.error == "unsupported_grant_type"


# --- the store ---


def test_a_store_file_from_before_credential_versions_gets_the_column(tmp_path):
    path = str(tmp_path / "older.db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE oauth_token (token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,"
        " grant_id TEXT NOT NULL, client_id TEXT NOT NULL, subject TEXT NOT NULL,"
        " scopes TEXT NOT NULL, resource TEXT, created_at REAL NOT NULL,"
        " expires_at REAL NOT NULL, revoked_at REAL)"
    )
    db.execute(
        "INSERT INTO oauth_token VALUES ('h', 'access', 'g', 'c', 'mcp:demo', 'read', NULL,"
        " 0, 9e9, NULL)"
    )
    db.commit()
    db.close()
    store = Store(path, FixedClock())
    store.save_token(
        "bss_at_new", kind="access", grant_id="g2", client_id="c", subject="mcp:demo",
        scopes=["read"], resource=None, credential_version=3, ttl=60,
    )  # fmt: skip
    assert store.token("bss_at_new", "access")["credential_version"] == 3
    assert [g["grant_id"] for g in store.grants()] == ["g2"]  # the old grant is dropped


class _FailingDeletes:
    """A store connection whose bulk delete fails half way (a full disk, say)."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def executemany(self, *args):
        raise sqlite3.OperationalError("database or disk is full")

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_a_failed_prune_rolls_back_and_keeps_every_client(mcp_settings):
    store = Store(mcp_settings.mcp_store, FixedClock())
    store.save_client("smoke-1", json.dumps({"client_name": "bss-mcp-smoke"}))
    real = store._db
    store._db = _FailingDeletes(real)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        store.prune_clients("bss-mcp-smoke")
    store._db = real
    assert store.client("smoke-1") is not None
    assert store.prune_clients("bss-mcp-smoke") == 1  # no transaction left open


# --- the ASGI helpers: disconnects and the replayed body ---


def test_the_replayed_body_is_sent_once_then_the_real_receive_answers():
    async def receive():
        return {"type": "http.disconnect"}

    async def run():
        replay = _replay(b"payload", receive)
        return [await replay(), await replay()]

    first, second = asyncio.run(run())
    assert first == {"type": "http.request", "body": b"payload", "more_body": False}
    assert second == {"type": "http.disconnect"}


def test_a_client_that_disconnects_mid_body_reaches_nothing():
    messages = iter(
        [{"type": "http.request", "body": b"{", "more_body": True}, {"type": "http.disconnect"}]
    )
    called, sent = [], []

    async def app(scope, receive, send):
        called.append(scope)

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(BodyLimit(app, limit=1024)({"type": "http"}, receive, send))
    assert called == [] and sent == []


def test_revoke_adds_an_empty_client_secret_to_a_post_and_leaves_other_methods_alone():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["method"], await receive(), await receive()))

    async def receive():
        return {"type": "http.disconnect"}

    async def run():
        replays = iter([{"type": "http.request", "body": b"token=t", "more_body": False}])

        async def body():
            return next(replays, {"type": "http.disconnect"})

        revoke = PublicClientRevoke(app)
        await revoke({"type": "http", "method": "POST"}, body, None)
        await revoke({"type": "http", "method": "GET"}, receive, None)

    asyncio.run(run())
    (post_method, post_body, after_post), (get_method, get_first, _) = seen
    assert post_method == "POST" and post_body["body"] == b"token=t&client_secret="
    assert after_post == {"type": "http.disconnect"}
    assert get_method == "GET" and get_first == {"type": "http.disconnect"}


# --- small units ---


def test_the_auth_caller_is_none_without_an_access_token():
    assert caller({}) is None
    assert caller({"user": UnauthenticatedUser()}) is None


@pytest.mark.parametrize(
    "stored",
    ["bcrypt.16384.8.1.c2FsdA.a2V5", "scrypt.not.a.number.c2FsdA.a2V5", "no-dots", ""],
)
def test_a_password_hash_of_another_scheme_or_shape_never_matches(stored):
    assert passwords.check_password("anything", stored) is False


def test_a_float_inside_a_list_is_found_by_its_path():
    assert schema.find_float({"items": [1, {"price": "2"}, {"price": 2.5}]}) == "items[2].price"
    assert schema.find_float([0, 1.0]) == "[1]"
    assert schema.find_float({"items": [1, "2"]}) is None


def test_a_passthrough_description_of_an_unknown_api_stays_as_written():
    text = "TMF999 thing, as the spec defines it"
    assert schema._document_description(text) == text


def test_a_schema_nesting_deeper_than_32_levels_is_refused():
    defs = {f"d{i}": {"$ref": f"#/$defs/d{i + 1}"} for i in range(40)}
    defs["d40"] = {"type": "string"}
    with pytest.raises(ValueError, match="nests too deep"):
        schema._inline({"$ref": "#/$defs/d0"}, defs, 0, ())


def test_an_unmapped_oauth_outcome_is_counted_as_other():
    assert metrics.oauth_result("something new") == "other"


def test_the_real_clock_reads_the_system_clocks():
    clock = Clock()
    assert abs(clock.now() - time.time()) < 5
    first = clock.monotonic()
    assert clock.monotonic() >= first


def test_a_get_route_that_is_not_a_tmf_list_is_drift():
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/plain")
    def plain() -> dict:
        return {}

    with pytest.raises(DriftError, match="/plain: not a TMF list \\(the probe answered 200\\)"):
        asyncio.run(list_filters(app, "/plain"))


# --- the entry points, as their processes run them ---


def _python(tmp_path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "MCP_STORE": str(tmp_path / "entry.db")}
    return subprocess.run(  # noqa: S603 - our own interpreter and module, fixed arguments
        [sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )


def test_the_uvicorn_entry_point_builds_the_app_from_the_environment(tmp_path):
    done = _python(
        tmp_path,
        "-c",
        "import bss_mcp.main as m; print(type(m.app).__name__, m.settings.mcp_store)",
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["Starlette", str(tmp_path / "entry.db")]


def test_the_token_tool_runs_as_a_module(tmp_path):
    created = _python(tmp_path, "-m", "bss_mcp.tokens", "create", "ci", "--scope", "read")
    assert created.returncode == 0, created.stderr
    listed = _python(tmp_path, "-m", "bss_mcp.tokens", "list")
    assert listed.returncode == 0 and "ci" in listed.stdout
    assert '"event": "token_create"' in created.stderr  # the audit line goes to stderr
    missing = _python(tmp_path, "-m", "bss_mcp.tokens", "revoke-grant", "nope")
    assert missing.returncode == 1 and "no live grant with id nope" in missing.stderr
