# ruff: noqa: E402 - PROMETHEUS_MULTIPROC_DIR must be set before the imports below
import logging
import os
import sys
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
from pathlib import Path

import hypothesis
import psycopg
import pytest
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings
from tests import scratch_db

pytest_plugins = ["loadtest.plan_capture"] if os.environ.get("BSS_PLAN_CAPTURE_DIR") else []

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
# no log-level poller thread in the lifespan of every TestClient (tests/ops/test_log_level.py
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
    _tables.update(written_tables(url))


def written_tables(url: str) -> set[str]:
    """The tables of the database at `url` that were written: rows inserted or updated
    (pg_stat_user_tables.n_tup_ins + n_tup_upd). An update counts: a one-row settings
    table is seeded by its migration, which the template clone does not count, and is
    only ever updated afterwards (#276).
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
        rows = conn.execute(
            "SELECT relname FROM pg_stat_user_tables WHERE n_tup_ins + n_tup_upd > 0"
        )
        return {row[0] for row in rows}


# --- data files testmon cannot follow (scripts/hook_selection.py, #344) ---
# The commit hook runs, for a staged data file, exactly the test files DATA lists for it.
# So a read of such a file by any other test file is refused here, at the read: the list
# cannot fall behind the tests. Reads while a module is collected (its imports) count for
# that module. A subprocess is not seen; a test that starts one names its data here itself.
# (This file is imported twice, as conftest and tests.conftest; only the plugin's copy
# is told which test runs, so the other's hook does nothing.)
sys.path.insert(0, str(Path("scripts").resolve()))
import hook_selection

_ROOT = str(Path.cwd()) + os.sep
_READS = ("open", "os.listdir", "os.scandir")
_reader: list[str | None] = [None]  # the test file now collected or run


def _audit(event: str, args: tuple) -> None:
    if event in _READS and _reader[0] is not None:
        hook_selection.check_read(args[0], _reader[0], _ROOT)


sys.addaudithook(_audit)


def pytest_collectstart(collector) -> None:
    if isinstance(collector, pytest.Module):
        _reader[0] = collector.nodeid


def pytest_collectreport(report) -> None:
    _reader[0] = None


def pytest_runtest_logstart(nodeid, location) -> None:
    _reader[0] = location[0]


def pytest_runtest_logfinish(nodeid, location) -> None:
    _reader[0] = None


# --- the tests make test leaves out (#424, docs/decisions/harness.md) ---
# `migration_history` and `slow` run in the hook when their paths change
# (scripts/hook_selection.py, MARKED), in the nightly run and in make test-all. A test that
# replays migrations (temp_db_url) carries `migration_history` unless it is kept here: the
# head and the round trip of every migration, the drill's refusal, an app check against an
# older schema, and the data tests of migrations not deployed yet. Once such a migration is
# deployed (make deployed), its tests move to `migration_history`.
LEFT_OUT = ("migration_history", "slow")
KEPT_REPLAYS = frozenset(
    {
        "tests/schema/test_migrations.py::test_alembic_upgrade_head_on_clean_database",
        "tests/schema/test_migrations.py::test_alembic_downgrade_then_upgrade_roundtrip",
        "tests/schema/test_migrations.py::test_migration_drill_rejects_an_unknown_mode",
        "tests/perf/test_worker_round_trips.py::"
        "test_an_older_schema_is_asked_again_and_a_later_migration_is_seen",
    }
)


def mark_errors(items) -> list[str]:
    """What is wrong with the marks of the collected `items`: a smoke test the default run
    would leave out, or a migration replay that is neither kept nor `migration_history`."""
    errors = []
    for item in items:
        marks = {mark.name for mark in item.iter_markers()}
        name = item.nodeid.split("::", 1)[0] + "::" + getattr(item, "originalname", item.name)
        if "smoke" in marks and marks & set(LEFT_OUT):
            left_out = sorted(marks & set(LEFT_OUT))
            errors.append(f"{item.nodeid}: a smoke test cannot also be {', '.join(left_out)}")
        replays = "temp_db_url" in getattr(item, "fixturenames", ())
        if replays and "migration_history" not in marks and name not in KEPT_REPLAYS:
            errors.append(
                f"{item.nodeid} replays migrations: mark it migration_history, or keep it in"
                " KEPT_REPLAYS (tests/conftest.py) while its migration is not deployed"
            )
    return errors


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items) -> None:
    """Before -m deselects anything, so every run checks every test."""
    errors = mark_errors(items)
    if errors:
        raise pytest.UsageError("\n".join(errors))


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


_admin_connection: list[psycopg.Connection] = []


def _admin() -> psycopg.Connection:
    """This process's connection for creating and dropping databases, opened once: a new
    one for every statement cost 30 ms each, twice a test (#344)."""
    if not _admin_connection or _admin_connection[0].closed:
        _admin_connection[:] = [psycopg.connect(ADMIN_URL, autocommit=True)]
    return _admin_connection[0]


def _admin_execute(statement) -> None:
    conn = _admin()
    try:
        conn.execute(statement)
    except psycopg.OperationalError:
        if not conn.broken:
            raise
        # the kept connection was lost (a test cut the server's sessions): once more
        _admin().execute(statement)


def _create_database(name: str, template: str | None = None) -> str:
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
    _admin_execute(statement)
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"


def _drop_database(name: str) -> None:
    from app import db

    # the app's pool for this database (app/db.py): closed first, else its workers would
    # keep reconnecting to a database that no longer exists
    db.close(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}")
    _admin_execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


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


def _migrate(name: str) -> None:
    """Migrates the empty database `name` to head and leaves no session connected to it."""
    from app import db

    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads it
    # alembic/env.py also configures logging from alembic.ini, which replaces the root
    # handlers. Only the worker that builds the template runs it, so put the logging back:
    # the workers must not differ. (While every worker migrated its own template, all of
    # them lost the app's redacting handler, which hid a wrong redaction, see logsetup.)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous
        db.close(url)


@pytest.fixture(scope="session")
def template_db() -> str:
    """The database at migration head that test databases are cloned from with
    CREATE DATABASE ... TEMPLATE, which copies files instead of replaying migrations.
    Built once for a set of migrations and kept (tests/scratch_db.py): every xdist worker
    used to migrate its own, 4.5 s alone and 9 to 30 s each when twelve did it at once.
    Nothing may stay connected to it: Postgres refuses to clone a database in use."""
    digest = scratch_db.migrations_digest(Path.cwd())
    return scratch_db.keep(ADMIN_URL, "template", digest, _migrate)


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


@pytest.fixture(params=["by-id", "walk"])
def match_path(request, monkeypatch):
    """Both ways a registry search page is cut (#330, app/repository/subscribers.py
    `matching`): from the probed matches read by id, or walking the order index when the
    probe hits its cap (a cap of 0: every non-empty search walks)."""
    from app.repository import subscribers

    if request.param == "walk":
        monkeypatch.setattr(subscribers, "MATCH_CAP", 0)
    return request.param


@cache
def _test_password_hash():
    """The test user's stored hash, with the smallest scrypt cost. A stored hash names its
    own cost (bss_mcp/passwords.py), so every test still signs in through the real session
    endpoint and password check; at the production cost that check alone took 110 ms of
    every test (#344). Users a test creates itself keep the production cost."""
    import hashlib

    from bss_mcp import passwords

    salt = b"test-user-salt--"
    key = hashlib.scrypt(b"test-password-123", salt=salt, n=2, r=8, p=1)
    return f"scrypt.2.8.1.{passwords._b64(salt)}.{passwords._b64(key)}"


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
    """Most tests buy prepaid lines on accounts without an address; tests/billing/test_tax_engine.py
    turns the gate (BSS_TAX_REQUIRE_SERVICE_ADDRESS, on by default) back on."""
    monkeypatch.setattr(settings, "tax_require_service_address", False)


@pytest.fixture(autouse=True)
def _no_account_pin_gate(monkeypatch) -> None:
    """Most tests swap SIMs, change numbers and edit contacts without a PIN;
    tests/care/test_account_security.py turns the gate (BSS_ACCOUNT_PIN_REQUIRED, on by default)
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
