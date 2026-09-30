import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings


@pytest.fixture
def temp_db_url(monkeypatch) -> Iterator[str]:
    """Create a throwaway database, point DATABASE_URL at it, drop it afterward."""
    admin_url = settings.database_url
    test_db = f"vpslab_test_{uuid.uuid4().hex[:8]}"

    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db)))

    test_url = admin_url.rsplit("/", 1)[0] + f"/{test_db}"
    monkeypatch.setenv("DATABASE_URL", test_url)

    try:
        yield test_url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(test_db))
            )


@pytest.fixture
def db_conn(temp_db_url) -> Iterator[psycopg.Connection]:
    """A connection to a freshly-migrated (head) throwaway database."""
    command.upgrade(Config("alembic.ini"), "head")
    conn = psycopg.connect(temp_db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def api(db_conn, temp_db_url, monkeypatch):
    """The real app on the throwaway database with a FixedClock, behind a client that
    validates every response against the TMF schemas (tests/apiclient.py).

    Only the clock is overridden: requests go through the production transaction
    dependency, pointed at the test database via settings.
    """
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from app.api.deps import get_clock
    from app.clock import FixedClock
    from app.main import app
    from tests.apiclient import TmfClient

    clock = FixedClock(datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    monkeypatch.setattr(settings, "database_url", temp_db_url)
    app.dependency_overrides[get_clock] = lambda: clock
    client = TmfClient(TestClient(app, raise_server_exceptions=False))
    client.clock = clock
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
