import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings

# Captured at import: the `api` fixture points settings.database_url at the test database,
# and creating or dropping databases must not connect to the one being dropped.
ADMIN_URL = settings.database_url


def _create_database(name: str, template: str | None = None) -> str:
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(statement)
    return ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"


def _drop_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def temp_db_url(monkeypatch) -> Iterator[str]:
    """An EMPTY throwaway database with DATABASE_URL pointed at it. For tests that run
    real migrations themselves; everything else wants `db_conn`."""
    test_db = f"vpslab_test_{uuid.uuid4().hex[:8]}"
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
    name = f"vpslab_template_{uuid.uuid4().hex[:8]}"
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
    test_db = f"vpslab_test_{uuid.uuid4().hex[:8]}"
    test_url = _create_database(test_db, template=template_db)
    monkeypatch.setenv("DATABASE_URL", test_url)
    try:
        yield test_url
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
    app.dependency_overrides[get_clock] = lambda: clock
    client = TmfClient(TestClient(app, raise_server_exceptions=False))
    client.clock = clock
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
