"""Structured request logging with a request ID and secret redaction (issue #273)."""

import asyncio
import io
import json
import logging

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import logsetup
from app.api import errors
from app.main import app
from app.worker import __main__ as worker_main

pytestmark = pytest.mark.smoke

SECRET = "S3CRET"


@pytest.fixture
def lines():
    """The JSON handler's output as parsed lines, at DEBUG for our loggers."""
    stream = io.StringIO()
    root = logging.getLogger()
    before = list(root.handlers)
    levels = {n: logging.getLogger(n).level for n in (*logsetup.OUR_LOGGERS, "httpx2", "psycopg")}
    old_level = root.level
    old_access = logging.getLogger("uvicorn.access").disabled
    handler = logsetup.configure("app", stream)
    for name in logsetup.OUR_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    logsetup.request_id.set(None)  # the worker leaves its wake-up ID set
    root.removeHandler(handler)
    root.handlers[:] = before
    root.setLevel(old_level)
    logging.getLogger("uvicorn.access").disabled = old_access
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def request_lines(read, path: str | None = None) -> list[dict]:
    return [
        e for e in read() if e["logger"] == "app.request" and (path is None or path in e["msg"])
    ]


def test_formatter_emits_one_line_of_json_with_the_fields(lines):
    token = logsetup.request_id.set("abc123")
    try:
        log = logging.getLogger("app.test")
        log.info("hello %s", "world", extra={"method": "GET", "status": 200})
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")
    finally:
        logsetup.request_id.reset(token)
    first, second = lines()[-2:]
    assert first["msg"] == "hello world"
    assert first["level"] == "INFO"
    assert first["logger"] == "app.test"
    assert first["process"] == "app"
    assert first["request_id"] == "abc123"
    assert (first["method"], first["status"]) == ("GET", 200)
    assert first["ts"].endswith("Z") and first["ts"][10] == "T" and len(first["ts"]) == 24
    assert second["level"] == "ERROR"
    assert "ValueError: boom" in second["exc"]
    assert "exc" not in first


def test_no_request_id_field_outside_a_request(lines):
    logsetup.request_id.set(None)
    logging.getLogger("app.test").info("plain")
    assert "request_id" not in lines()[-1]


def test_response_carries_a_generated_request_id(lines):
    with TestClient(app) as client:
        response = client.get("/")
    rid = response.headers["x-request-id"]
    assert len(rid) == 32 and logsetup.VALID_REQUEST_ID.match(rid)


def test_a_valid_inbound_request_id_is_echoed(lines):
    with TestClient(app) as client:
        response = client.get("/", headers={"X-Request-Id": "trace.me_1-x"})
    assert response.headers["x-request-id"] == "trace.me_1-x"
    assert request_lines(lines)[-1]["request_id"] == "trace.me_1-x"


@pytest.mark.parametrize("bad", ["x" * 65, "has space", "semi;colon", "ünï"])
def test_an_invalid_inbound_request_id_is_replaced(lines, bad):
    with TestClient(app) as client:
        response = client.get("/", headers={"X-Request-Id": bad.encode("utf-8")})
    rid = response.headers["x-request-id"]
    assert rid != bad and logsetup.VALID_REQUEST_ID.match(rid)


def test_one_request_line_with_route_status_and_duration(lines):
    with TestClient(app) as client:
        client.get("/nothing-here?q=" + SECRET)
        client.get("/")
    entry = request_lines(lines)[0]
    assert entry["level"] == "INFO"
    assert (entry["method"], entry["route"]) == ("GET", "unmatched")
    assert entry["status"] == 404 or entry["status"] == 401
    assert isinstance(entry["duration_ms"], float) and entry["duration_ms"] >= 0
    assert all(SECRET not in line for line in map(json.dumps, lines()))  # no query string


def test_the_route_is_the_template_not_the_path(lines):
    probe = FastAPI()

    @probe.get("/things/{thing_id}")
    def thing(thing_id: str) -> dict:
        return {"id": thing_id}

    probe.add_middleware(logsetup.RequestLogMiddleware)
    with TestClient(probe) as client:
        assert client.get("/things/42").status_code == 200
    entry = request_lines(lines)[-1]
    assert entry["route"] == "/things/{thing_id}"
    assert entry["status"] == 200


def test_health_and_metrics_do_not_log_at_info(lines):
    for name in logsetup.OUR_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
    with TestClient(app) as client:
        client.get("/healthz")
        client.get("/metrics")
    assert request_lines(lines) == []


def test_a_forced_500_logs_with_the_id_of_the_response_header(lines):
    probe = FastAPI()
    errors.install(probe)

    @probe.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    probe.add_middleware(logsetup.RequestLogMiddleware)
    with TestClient(probe, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-Id": "r-500"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "r-500"
    entries = lines()
    unhandled = [e for e in entries if e["logger"] == "app.api"]
    assert unhandled and unhandled[0]["request_id"] == "r-500"
    assert "RuntimeError: kaboom" in unhandled[0]["exc"]
    line = request_lines(lines)[-1]
    assert (line["level"], line["status"], line["request_id"]) == ("ERROR", 500, "r-500")


def test_nothing_secret_is_logged_at_debug(lines):
    headers = {
        "Authorization": f"Bearer {SECRET}",
        "Cookie": f"session={SECRET}",
        "X-Bss-Service": SECRET,
        "Content-Type": "application/json",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            f"/tmf-api/partyManagement/v5/individual?token={SECRET}",
            headers=headers,
            content=json.dumps({"password": SECRET}),
        )
        client.post("/internal/v1/session", headers=headers, json={"password": SECRET})
    log = logging.getLogger("app.test")
    log.debug("password=%s", SECRET)
    log.debug('body {"password": "%s"}', SECRET)
    log.debug("Authorization: Bearer %s", SECRET)
    log.debug("Cookie: session=%s; other=1", SECRET)
    log.debug("X-Bss-Service: %s", SECRET)
    log.info("x", extra={"operator": f"token={SECRET}"})
    try:
        raise ValueError(f"bad password={SECRET}")
    except ValueError:
        log.exception("failed")
    out = [json.dumps(entry) for entry in lines()]
    assert len(out) > 7
    assert [line for line in out if SECRET in line] == []
    assert any("[redacted]" in line for line in out)


@pytest.mark.parametrize("name", ["psycopg", "httpx2", "httpcore", "asyncio", "multipart"])
def test_third_party_loggers_stay_at_warning(lines, name):
    logging.getLogger("psycopg").setLevel(logging.NOTSET)
    assert logging.getLogger().level == logging.WARNING
    assert not logging.getLogger(name).isEnabledFor(logging.INFO)
    assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)
    logging.getLogger(name).debug("sql parameters: %s", SECRET)
    assert all(SECRET not in json.dumps(entry) for entry in lines())


def test_uvicorn_is_routed_through_the_json_handler(lines):
    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("uvicorn.error").handlers == []
    logging.getLogger("uvicorn.error").info("started")
    assert lines()[-1]["logger"] == "uvicorn.error"


def test_the_worker_sets_one_id_per_wake_up(monkeypatch, lines, capsys):
    seen: list[str | None] = []

    class Conn:
        closed = False

        def close(self) -> None:
            pass

    def fake_tick(conn, **kwargs):
        seen.append(logsetup.request_id.get())
        logging.getLogger("app.worker").info("working")
        return "ok"

    monkeypatch.setattr(worker_main.db, "connect", lambda: Conn())
    monkeypatch.setattr(worker_main, "tick", fake_tick)
    monkeypatch.setattr(worker_main, "check_run_at", lambda value: None)
    monkeypatch.setattr(worker_main, "clock_from_environment", lambda: None)
    assert worker_main.main(["--once"]) == 0
    assert worker_main.main(["--once"]) == 0
    assert all(i and i.startswith("wake-") and len(i) == 13 for i in seen)
    assert seen[0] != seen[1]
    # main() calls configure("worker") itself, so its lines go to stdout, not the fixture's stream
    out = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    working = [e for e in out if e["msg"] == "working"]
    assert {e["process"] for e in working} == {"worker"}
    assert [e["request_id"] for e in working] == seen


def test_mcp_forwards_the_request_id_to_the_app(lines):
    from bss_mcp.bss import Bss

    got: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        got.append(request)
        return httpx2.Response(200, json={})

    bss = Bss("http://bss.test", 5.0, httpx2.MockTransport(handler), service_secret="svc")

    async def calls() -> None:
        token = logsetup.request_id.set("mcp-call-1")
        try:
            await bss.request("GET", "/x", principal="p")
            await bss.credential_check("u", "pw")
        finally:
            logsetup.request_id.reset(token)
        await bss.request("GET", "/x", principal="p")

    asyncio.run(calls())
    assert [r.headers.get("x-request-id") for r in got] == ["mcp-call-1", "mcp-call-1", None]
