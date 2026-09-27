from contextlib import contextmanager

import psycopg

from app.config import settings


@contextmanager
def connection():
    conn = psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout)
    try:
        yield conn
    finally:
        conn.close()


def ping() -> bool:
    """Cheap liveness probe for the database. Used by /healthz."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)
