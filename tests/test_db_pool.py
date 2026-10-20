"""The API's connection pool (app/db.py): a connection comes back as a fresh one would."""

import psycopg
import pytest

from app import db
from app.config import settings


@pytest.fixture
def pooled(migrated_db_url, monkeypatch):
    monkeypatch.setattr(settings, "database_url", migrated_db_url)
    monkeypatch.setattr(settings, "db_pool_size", 1)  # every checkout gets the same one
    yield migrated_db_url


def _backend(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT pg_backend_pid()").fetchone()[0]


def test_a_connection_is_reused(pooled):
    with db.connection() as conn:
        first = _backend(conn)
    with db.connection() as conn:
        assert _backend(conn) == first


def test_uncommitted_work_does_not_reach_the_next_request(pooled):
    with db.connection() as conn:
        conn.execute("CREATE TABLE left_open (id int)")  # never committed
    with db.connection() as conn:
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert conn.execute("SELECT to_regclass('left_open')").fetchone() == (None,)


def test_a_failed_transaction_does_not_reach_the_next_request(pooled):
    with pytest.raises(psycopg.errors.DivisionByZero), db.connection() as conn:
        conn.execute("SELECT 1 / 0")
    with db.connection() as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_a_connection_the_server_ended_is_replaced(pooled):
    # a database restart (the nightly reboot) ends every pooled connection
    with db.connection() as conn:
        first = _backend(conn)
    with psycopg.connect(pooled, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (first,))
    with db.connection() as conn:
        assert _backend(conn) != first
    assert db.ping()
