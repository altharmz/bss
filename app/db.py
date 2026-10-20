import atexit
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from functools import partial

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import set_json_loads
from psycopg_pool import ConnectionPool

from app.config import settings

# json/jsonb read back with exact numbers: a stored document (a party's details) may hold
# numbers, and Decimal is what the API writes them from (app/api/tmfjson.py)
_loads = partial(json.loads, parse_float=Decimal)

# One pool per database URL (docs/decisions/infra.md, "DB connections per request"): the
# app has one; tests point settings.database_url at a database per test, and
# conftest closes that database's pool before dropping it (`close`).
_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()
_OPEN = (TransactionStatus.INTRANS, TransactionStatus.INERROR)


def _configure(conn: psycopg.Connection) -> None:
    set_json_loads(_loads, conn)


def _pool(url: str) -> ConnectionPool:
    with _lock:
        pool = _pools.get(url)
        if pool is None:
            pool = ConnectionPool(
                url,
                kwargs={"connect_timeout": settings.db_connect_timeout},
                min_size=1,
                max_size=settings.db_pool_size,
                # waiting for a free connection is bounded like a connect
                timeout=settings.db_connect_timeout,
                configure=_configure,
                # a connection broken by a database restart is replaced, not handed out
                check=ConnectionPool.check_connection,
                open=True,
            )
            _pools[url] = pool
        return pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection. Whatever the caller left uncommitted is rolled back, as
    closing a connection did."""
    pool = _pool(settings.database_url)
    conn = pool.getconn()
    try:
        yield conn
    finally:
        try:
            if conn.info.transaction_status in _OPEN:
                conn.rollback()
        except psycopg.Error:
            pass  # a broken connection: putconn discards it
        pool.putconn(conn)


def close(url: str | None = None) -> None:
    """Close the pool of `url`, or every pool."""
    with _lock:
        if url is None:
            pools = list(_pools.values())
            _pools.clear()
        else:
            pools = [_pools.pop(url)] if url in _pools else []
    for pool in pools:
        pool.close()


atexit.register(close)


def ping() -> bool:
    """Cheap liveness probe for the database. Used by /healthz."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)
