import json
from contextlib import contextmanager
from decimal import Decimal
from functools import partial

import psycopg
from psycopg.types.json import set_json_loads

from app.config import settings

# json/jsonb read back with exact numbers: a stored document (a party's details) may hold
# numbers, and Decimal is what the API writes them from (app/api/tmfjson.py)
_loads = partial(json.loads, parse_float=Decimal)


@contextmanager
def connection():
    conn = psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout)
    set_json_loads(_loads, conn)
    try:
        yield conn
    finally:
        conn.close()


def ping() -> bool:
    """Cheap liveness probe for the database. Used by /healthz."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)
