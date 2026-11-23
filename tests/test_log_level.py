"""The runtime log level: effective level, admin endpoints, the connector's service path, the
pollers (issue #274)."""

import io
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import logsetup
from app.config import settings
from app.logsetup import DbLevelSource, LevelPoller, effective_level
from app.main import app
from bss_mcp.bss import Bss
from bss_mcp.clock import FixedClock as McpClock
from bss_mcp.server import build_app
from bss_mcp.store import Store
from tests import contract
from tests.timeline import NOW

LOGGING = "/internal/v1/logging"
SERVICE_PATH = "/internal/v1/service/log-level"
SERVICE = {"X-Bss-Service": "test-service-credential"}
SECRET = "S3CRET"
AT = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def levels():
    """Restore our loggers' levels (a poller changes them) and the poller's own state."""
    names = (*logsetup.OUR_LOGGERS, "psycopg", "httpx2")
    saved = {n: logging.getLogger(n).level for n in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


# --- the rule ---


@pytest.mark.smoke
@pytest.mark.parametrize(
    ("base", "until", "now", "expected"),
    [
        (None, None, AT, "INFO"),  # env
        ("WARNING", None, AT, "WARNING"),
        ("INFO", AT + timedelta(seconds=1), AT, "DEBUG"),  # debug active
        ("WARNING", AT + timedelta(minutes=5), AT, "DEBUG"),  # beats the base
        (None, AT + timedelta(minutes=5), AT, "DEBUG"),
        ("WARNING", AT, AT, "WARNING"),  # exactly at debug_until it is over (half-open)
        ("WARNING", AT - timedelta(seconds=1), AT, "WARNING"),  # expired
        (None, AT - timedelta(days=1), AT, "INFO"),  # expired, no base: env
    ],
)
def test_effective_level(base, until, now, expected):
    assert effective_level(base, until, "info", now) == expected


# --- the admin endpoints ---


def audit_rows(db_conn):
    db_conn.rollback()
    return db_conn.execute(
        "SELECT operator, old_base_level, new_base_level, old_debug_until, new_debug_until,"
        " changed_at FROM log_setting_change ORDER BY changed_at, id"
    ).fetchall()


def test_the_defaults_follow_the_environment(api):
    body = api.get(LOGGING).json()
    assert body == {
        "baseLevel": None,
        "envLevel": settings.log_level.upper(),
        "effectiveLevel": settings.log_level.upper(),
        "debugUntil": None,
        "updatedAt": None,
        "updatedBy": None,
    }


def test_debug_is_started_ended_and_audited(api, db_conn, caplog):
    caplog.set_level(logging.WARNING, logger="app.logging")
    started = api.put(LOGGING, {"debugMinutes": 30})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["effectiveLevel"] == "DEBUG"
    assert datetime.fromisoformat(body["debugUntil"]) == NOW + timedelta(minutes=30)
    assert (body["updatedBy"], body["baseLevel"]) == ("i.petrova", None)
    assert api.get(LOGGING).json() == body
    ended = api.put(LOGGING, {"debugMinutes": 0}).json()
    assert ended["debugUntil"] is None
    assert ended["effectiveLevel"] == settings.log_level.upper()
    rows = audit_rows(db_conn)
    # both rows carry the fixed clock's instant, so they have no order: compare as a set
    assert sorted(((r[0], r[3], r[4]) for r in rows), key=str) == sorted(
        [
            ("i.petrova", None, NOW + timedelta(minutes=30)),
            ("i.petrova", NOW + timedelta(minutes=30), None),
        ],
        key=str,
    )
    assert all(r[5] == NOW for r in rows)
    changes = [r for r in caplog.records if r.name == "app.logging"]
    assert len(changes) == 2 and all("i.petrova" in r.getMessage() for r in changes)


def test_the_base_level_is_set_and_cleared_and_keeps_the_debug(api, db_conn):
    api.put(LOGGING, {"debugMinutes": 10})
    body = api.put(LOGGING, {"baseLevel": "WARNING"}).json()
    assert (body["baseLevel"], body["effectiveLevel"]) == ("WARNING", "DEBUG")  # debug kept
    api.put(LOGGING, {"debugMinutes": 0})
    assert api.get(LOGGING).json()["effectiveLevel"] == "WARNING"
    assert api.put(LOGGING, {"baseLevel": None}).json()["baseLevel"] is None
    assert api.get(LOGGING).json()["effectiveLevel"] == settings.log_level.upper()
    # one fixed instant: the rows have no order, compare them as a set
    assert sorted(((r[1], r[2]) for r in audit_rows(db_conn)), key=str) == sorted(
        [
            (None, None),
            (None, "WARNING"),
            ("WARNING", "WARNING"),
            ("WARNING", None),
        ],
        key=str,
    )


def test_the_maximum_debug_is_120_minutes(api):
    assert api.put(LOGGING, {"debugMinutes": 120}).status_code == 200
    assert api.put(LOGGING, {"debugMinutes": 121}).status_code == 400


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"debugMinutes": 121},
        {"debugMinutes": -1},
        {"debugMinutes": "30"},
        {"debugMinutes": 1.5},
        {"debugMinutes": True},
        {"baseLevel": "DEBUG"},
        {"baseLevel": "TRACE"},
        {"unknown": 1},
        {"debugMinutes": 5, "unknown": 1},
    ],
)
def test_a_bad_change_is_a_400_and_changes_nothing(api, db_conn, bad):
    before = api.get(LOGGING).json()
    response = api.put(LOGGING, bad)
    assert response.status_code == 400, response.text
    assert api.get(LOGGING).json() == before
    assert audit_rows(db_conn) == []


@pytest.fixture
def raw(api):
    with TestClient(app, headers={"X-Bss-Request": "1"}, raise_server_exceptions=False) as client:
        yield client


def test_only_an_administrator_changes_or_reads_it(raw):
    from tests.builders.users import (
        activate,
        create,
        login,
    )

    assert login(raw).status_code == 200  # the administrator
    create(raw, "alice", "user")
    raw.cookies.clear()
    activate(raw, "alice")
    assert raw.get(LOGGING).status_code == 403
    assert raw.put(LOGGING, json={"debugMinutes": 5}).status_code == 403
    raw.cookies.clear()
    # the service credential is not an administrator either
    assert raw.get(LOGGING, headers=SERVICE).status_code == 403
    assert raw.put(LOGGING, json={"debugMinutes": 5}, headers=SERVICE).status_code == 403
    assert login(raw).status_code == 200
    assert raw.get(LOGGING).json()["effectiveLevel"] == settings.log_level.upper()
    assert raw.put(LOGGING, json={"debugMinutes": 5}).status_code == 200


def test_the_change_is_in_the_contract(api):
    contract.assert_valid("INTERNAL", "LogSettings", api.put(LOGGING, {"debugMinutes": 5}).json())


# --- the connector's service path ---


def test_the_service_credential_alone_reads_the_level(api, raw):
    api.put(LOGGING, {"baseLevel": "WARNING", "debugMinutes": 15})
    response = raw.get(SERVICE_PATH, headers=SERVICE)
    assert response.status_code == 200, response.text
    contract.assert_valid("INTERNAL", "ServiceLogLevel", response.json())
    body = response.json()
    assert (body["baseLevel"], body["effectiveLevel"]) == ("WARNING", "DEBUG")
    assert datetime.fromisoformat(body["debugUntil"]) == NOW + timedelta(minutes=15)


def test_nothing_else_reads_the_service_path(raw, monkeypatch):
    from tests.builders.users import (
        login,
    )

    monkeypatch.setattr(settings, "bss_smoke_secret", "smoke-credential")
    assert raw.get(SERVICE_PATH).status_code == 401  # nobody
    assert raw.get(SERVICE_PATH, headers={"X-Bss-Service": "wrong"}).status_code == 401
    assert raw.get(SERVICE_PATH, headers={"X-Bss-Service": "smoke-credential"}).status_code == 403
    assert raw.post(SERVICE_PATH, headers=SERVICE).status_code == 405  # a read-only path
    assert login(raw).status_code == 200
    assert raw.get(SERVICE_PATH).status_code == 403  # an administrator's session


def test_none_of_the_three_is_a_tool():
    from bss_mcp.operations import AUTH_OPERATIONS, EXCLUDED

    assert ("GET", SERVICE_PATH) in AUTH_OPERATIONS
    assert {("GET", LOGGING), ("PUT", LOGGING)} <= EXCLUDED


# --- the pollers ---


class Source:
    """A fetch that can be told to fail."""

    def __init__(self):
        self.value: tuple = (None, None)
        self.error: Exception | None = None

    def __call__(self):
        if self.error:
            raise self.error
        return self.value


def our_levels() -> set[int]:
    return {logging.getLogger(n).level for n in logsetup.OUR_LOGGERS}


@pytest.mark.smoke
def test_a_poll_sets_debug_on_our_loggers_only_and_expiry_restores_the_base(levels):
    now = [AT]
    source = Source()
    poller = LevelPoller(lambda: now[0], "info", source)
    source.value = ("WARNING", AT + timedelta(minutes=10))
    poller.poll_once()
    assert our_levels() == {logging.DEBUG}
    assert logging.getLogger("psycopg").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx2").getEffectiveLevel() == logging.WARNING
    now[0] = AT + timedelta(minutes=10)  # the instant it is over
    poller.poll_once()
    assert our_levels() == {logging.WARNING}


def test_the_row_in_the_database_is_what_the_poller_reads(api, db_conn, levels):
    db_conn.execute("UPDATE log_setting SET debug_until = %s", (AT + timedelta(hours=1),))
    db_conn.commit()
    source = DbLevelSource(settings.database_url)
    now = [AT]
    poller = LevelPoller(lambda: now[0], "info", source)
    try:
        poller.poll_once()
        assert our_levels() == {logging.DEBUG}
        now[0] = AT + timedelta(hours=2)
        poller.poll_once()
        assert our_levels() == {logging.INFO}
    finally:
        source.close()


def test_an_outage_keeps_the_level_and_logs_once(levels, caplog):
    caplog.set_level(logging.WARNING, logger="app.logsetup")
    now = [AT]
    source = Source()
    poller = LevelPoller(lambda: now[0], "info", source)
    source.value = (None, AT + timedelta(minutes=10))
    poller.poll_once()
    assert our_levels() == {logging.DEBUG}
    source.error = ConnectionError("database down")
    caplog.clear()
    for _ in range(3):
        poller.poll_once()
    assert our_levels() == {logging.DEBUG}  # kept
    assert len([r for r in caplog.records if "unreadable" in r.getMessage()]) == 1
    now[0] = AT + timedelta(minutes=11)  # still down, but the debug expired by our own clock
    poller.poll_once()
    assert our_levels() == {logging.INFO}
    source.error = None
    source.value = (None, None)
    poller.poll_once()
    assert any("readable again" in r.getMessage() for r in caplog.records)


def test_each_change_is_logged_once_at_warning(levels, caplog):
    caplog.set_level(logging.WARNING, logger="app.logsetup")
    poller = LevelPoller(lambda: AT, "info", Source())
    poller.observe(None, AT + timedelta(minutes=5))
    poller.observe(None, AT + timedelta(minutes=5))  # no change, no line
    poller.observe("WARNING", None)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert lines[0].startswith("log level INFO -> DEBUG until ")
    assert lines[1] == "log level DEBUG -> WARNING"


def test_the_thread_polls_until_stopped(levels):
    source = Source()
    source.value = (None, AT + timedelta(hours=1))
    poller = LevelPoller(lambda: AT, "info", source, interval=0.05).start()
    try:
        deadline = time.monotonic() + 3
        while our_levels() != {logging.DEBUG} and time.monotonic() < deadline:
            time.sleep(0.02)
        assert our_levels() == {logging.DEBUG}
    finally:
        poller.stop()


def test_no_poller_when_the_interval_is_zero(monkeypatch):
    monkeypatch.setattr(settings, "log_poll_seconds", 0)
    assert logsetup.start_db_poller(lambda: AT) is None


# --- the connector ---


def test_the_connector_reads_the_level_from_the_app():
    def answer(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == SERVICE_PATH
        assert request.headers["x-bss-service"] == "svc"
        assert "x-remote-user" not in request.headers
        return httpx2.Response(
            200,
            json={
                "baseLevel": "WARNING",
                "effectiveLevel": "DEBUG",
                "debugUntil": "2026-11-09T12:30:00Z",
            },
        )

    import asyncio

    bss = Bss("http://bss.test", 5.0, httpx2.MockTransport(answer), service_secret="svc")
    assert asyncio.run(bss.log_level()) == ("WARNING", AT + timedelta(minutes=30))


def test_the_connectors_lifespan_applies_the_level(mcp_settings, levels):
    def answer(request: httpx2.Request) -> JSONResponse:
        return JSONResponse(
            {"baseLevel": "WARNING", "effectiveLevel": "WARNING", "debugUntil": None}
        )

    bss = Starlette(
        routes=[
            Route("/", lambda request: JSONResponse({"service": "bss", "status": "ok"})),
            Route(SERVICE_PATH, answer),
        ]
    )
    mcp_settings.log_poll_seconds = 1
    clock = McpClock()
    connector = build_app(
        mcp_settings,
        clock=clock,
        store=Store(mcp_settings.mcp_store, clock),
        transport=httpx2.ASGITransport(app=bss),
    )
    with TestClient(connector):
        deadline = time.monotonic() + 5
        while our_levels() != {logging.WARNING} and time.monotonic() < deadline:
            time.sleep(0.05)
        assert our_levels() == {logging.WARNING}
    assert logging.getLogger("psycopg").getEffectiveLevel() == logging.WARNING


# --- secrets at DEBUG, raised through the mechanism ---


def test_nothing_secret_is_logged_when_debug_comes_from_the_database(raw, db_conn, levels):
    db_conn.execute("UPDATE log_setting SET debug_until = %s", (AT + timedelta(hours=1),))
    db_conn.commit()
    stream = io.StringIO()
    root = logging.getLogger()
    handler = logsetup.configure("app", stream)
    source = DbLevelSource(settings.database_url)
    try:
        LevelPoller(lambda: AT, "info", source).poll_once()
        headers = {
            "Authorization": f"Bearer {SECRET}",
            "Cookie": f"session={SECRET}",
            "X-Bss-Service": SECRET,
        }
        raw.post(
            f"/internal/v1/session?token={SECRET}",
            headers=headers,
            json={"username": "x", "password": SECRET},
        )
        raw.put(LOGGING, headers=headers, content=json.dumps({"password": SECRET}))
        logging.getLogger("app.test").debug("password=%s", SECRET)
        out = stream.getvalue()
        assert '"level": "DEBUG"' in out
        assert SECRET not in out
    finally:
        source.close()
        root.removeHandler(handler)
