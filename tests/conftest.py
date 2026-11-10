# ruff: noqa: E402 - PROMETHEUS_MULTIPROC_DIR must be set before the imports below
import os
import tempfile

# app/metrics.py counts in Prometheus multiprocess mode (#288). Each test process (xdist
# worker) gets its own directory, set before anything imports prometheus_client, so one
# process's counters never show up in another's scrape. Once per process: a second import
# of this file (as tests.conftest) must not move the directory under the open metrics.
if os.environ.get("BSS_PROMETHEUS_PID") != str(os.getpid()):
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = tempfile.mkdtemp(prefix="bss-prometheus-")
    os.environ["BSS_PROMETHEUS_PID"] = str(os.getpid())

import time
from collections.abc import Iterator
from functools import cache

import hypothesis
import psycopg
import pytest
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings
from tests import scratch_db

# Hypothesis profiles, picked by HYPOTHESIS_PROFILE (the Makefile sets it): `dev` for make
# test and the pre-commit hook, `ci` for make verify. No example database: /app is not
# writable in the container, and a failure prints its example anyway.
for _name, _examples in (("dev", 50), ("ci", 200)):
    hypothesis.settings.register_profile(
        _name,
        max_examples=_examples,
        deadline=None,
        database=None,
        suppress_health_check=[hypothesis.HealthCheck.too_slow],
    )
hypothesis.settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# Captured at import: the `api` fixture points settings.database_url at the test database,
# and creating or dropping databases must not connect to the one being dropped.
ADMIN_URL = settings.database_url
# no log-level poller thread in the lifespan of every TestClient (tests/test_log_level.py
# drives LevelPoller itself)
settings.log_poll_seconds = 0

# --- "tested" for the progress tracker (make progress; tests/progress_collect.py) ---
# Set by the collector only, so make test is unchanged. Per test, it records the requests
# sent to the app (method, path - any TestClient of app.main.app: TmfClient, and the raw
# clients of test_health) and the tables of the test's database that got rows (the
# database's own statistics, read before it is dropped). The `_progress_record` fixture
# hands both to the collector in the test's user_properties; the collector counts a test
# only if it passed.
PROGRESS_RECORD = os.environ.get("BSS_PROGRESS_RECORD") == "1"
_requests: set[tuple[str, str]] = set()
_tables: set[str] = set()

if PROGRESS_RECORD:
    from urllib.parse import urlsplit

    from starlette.testclient import TestClient

    _send = TestClient.request

    def _recording_request(self, method, url, *args, **kwargs):
        from app.main import app

        if self.app is app:
            _requests.add((method.upper(), urlsplit(str(url)).path))
        return _send(self, method, url, *args, **kwargs)

    TestClient.request = _recording_request


def _record_written_tables(url: str) -> None:
    """The tables of the database at `url` that got rows (pg_stat_user_tables.n_tup_ins).
    A backend reports its counts when it ends, before it leaves pg_stat_activity: wait
    for every other session of the database to be gone first (5 s at most)."""
    with psycopg.connect(url, autocommit=True) as conn:
        deadline = time.monotonic() + 5
        while conn.execute(
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ).fetchone()[0]:
            if time.monotonic() > deadline:
                raise RuntimeError("progress: sessions of the test database did not end")
            time.sleep(0.01)
        rows = conn.execute("SELECT relname FROM pg_stat_user_tables WHERE n_tup_ins > 0")
        _tables.update(row[0] for row in rows)


def pytest_sessionstart(session) -> None:
    """Drops the databases earlier runs left behind (tests/scratch_db.py). Once per run:
    not in the xdist workers, which start while their controller's session is open."""
    if hasattr(session.config, "workerinput"):
        return
    try:
        scratch_db.drop_stale(ADMIN_URL)
    except psycopg.OperationalError:
        pass  # no database server: the tests that need one say so themselves


@pytest.fixture(autouse=True)
def _progress_record(request) -> Iterator[None]:
    """Autouse, so it is set up first and torn down last - after the test database has
    been read and dropped."""
    if not PROGRESS_RECORD:
        yield
        return
    _requests.clear()
    _tables.clear()
    yield
    request.node.user_properties.append(
        ("progress", {"requests": sorted(_requests), "tables": sorted(_tables)})
    )


def _create_database(name: str, template: str | None = None) -> str:
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(statement)
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"


def _drop_database(name: str) -> None:
    from app import db

    # the app's pool for this database (app/db.py): closed first, else its workers would
    # keep reconnecting to a database that no longer exists
    db.close(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}")
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def temp_db_url(monkeypatch) -> Iterator[str]:
    """An EMPTY throwaway database with DATABASE_URL pointed at it. For tests that run
    real migrations themselves; everything else wants `db_conn`."""
    test_db = scratch_db.name("test")
    test_url = _create_database(test_db)
    monkeypatch.setenv("DATABASE_URL", test_url)
    try:
        yield test_url
    finally:
        _drop_database(test_db)


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    """Migrated to head once per session. Test databases are cloned from it with
    CREATE DATABASE ... TEMPLATE, which copies files instead of replaying migrations.
    Nothing may stay connected to it: Postgres refuses to clone a database in use."""
    name = scratch_db.name("template")
    url = _create_database(name)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads it
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous
    try:
        yield name
    finally:
        _drop_database(name)


@pytest.fixture
def migrated_db_url(template_db, monkeypatch) -> Iterator[str]:
    """A throwaway database at migration head, cloned from the session template."""
    test_db = scratch_db.name("test")
    test_url = _create_database(test_db, template=template_db)
    monkeypatch.setenv("DATABASE_URL", test_url)
    try:
        yield test_url
    finally:
        try:
            if PROGRESS_RECORD:
                from app import db

                db.close(test_url)  # its sessions must end before the statistics are read
                _record_written_tables(test_url)
        finally:
            _drop_database(test_db)


@pytest.fixture
def db_conn(migrated_db_url) -> Iterator[psycopg.Connection]:
    """A connection to a throwaway database at migration head."""
    conn = psycopg.connect(migrated_db_url)
    try:
        yield conn
    finally:
        conn.close()


@cache
def _test_password_hash():
    from bss_mcp.passwords import hash_password

    return hash_password("test-password-123")


def install_test_user(conn):
    from tests.timeline import NOW

    conn.execute(
        "INSERT INTO app_user (username, role, password_hash, must_change_password,"
        " created_at, updated_at) VALUES ('i.petrova', 'admin', %s, false, %s, %s)",
        (_test_password_hash(), NOW, NOW),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _no_service_address_gate(monkeypatch) -> None:
    """Most tests buy prepaid lines on accounts without an address; tests/test_tax_engine.py
    turns the gate (BSS_TAX_REQUIRE_SERVICE_ADDRESS, on by default) back on."""
    monkeypatch.setattr(settings, "tax_require_service_address", False)


@pytest.fixture(autouse=True)
def _no_account_pin_gate(monkeypatch) -> None:
    """Most tests swap SIMs, change numbers and edit contacts without a PIN;
    tests/test_account_security.py turns the gate (BSS_ACCOUNT_PIN_REQUIRED, on by default)
    back on."""
    monkeypatch.setattr(settings, "account_pin_required", False)


@pytest.fixture
def api(db_conn, migrated_db_url, monkeypatch):
    """The real app on the throwaway database with a FixedClock, behind a client that
    validates every response against the TMF schemas (tests/apiclient.py).

    Only the clock is overridden: requests go through the production transaction
    dependency, pointed at the test database via settings.
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_clock
    from app.clock import FixedClock
    from app.main import app
    from tests.apiclient import TmfClient
    from tests.timeline import NOW

    clock = FixedClock(NOW)
    monkeypatch.setattr(settings, "database_url", migrated_db_url)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "bss_service_secret", "test-service-credential")
    install_test_user(db_conn)
    app.dependency_overrides[get_clock] = lambda: clock
    # Inside `with`, the client keeps one event-loop thread for all its requests; without
    # it, every request starts its own (12.5 ms against 6.3 ms a request, measured).
    try:
        with TestClient(app, raise_server_exceptions=False, headers={"X-Bss-Request": "1"}) as raw:
            assert (
                raw.post(
                    "/internal/v1/session",
                    json={
                        "username": "i.petrova",
                        "password": "test-password-123",
                    },
                ).status_code
                == 200
            )
            client = TmfClient(raw)
            client.clock = clock
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def mcp_settings(tmp_path):
    """The connector's settings for a test; change them before using `mcp`."""
    from bss_mcp.config import Settings

    return Settings(
        bss_service_secret="test-service-credential",
        bss_api_url="http://bss.test",
        mcp_public_url="https://mcp.test",
        mcp_store=str(tmp_path / "mcp.db"),
        mcp_identity_wait=0,
        log_poll_seconds=0,
    )


@pytest.fixture
def mcp(api, mcp_settings):
    """The MCP connector (bss_mcp) in process, in front of the `api` fixture's app."""
    import httpx2
    from starlette.testclient import TestClient

    from app.main import app
    from bss_mcp.clock import FixedClock
    from bss_mcp.server import build_app
    from bss_mcp.store import Store
    from tests.mcpclient import Mcp

    clock = FixedClock()
    store = Store(mcp_settings.mcp_store, clock)
    connector = build_app(
        mcp_settings, clock=clock, store=store, transport=httpx2.ASGITransport(app=app)
    )
    with TestClient(connector, base_url="https://mcp.test") as client:
        yield Mcp(client, store, clock)
