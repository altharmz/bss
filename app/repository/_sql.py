from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]


def fetch_one(conn: psycopg.Connection, query: str, params: Any = None) -> Row | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def fetch_all(conn: psycopg.Connection, query: str, params: Any = None) -> list[Row]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def returning(conn: psycopg.Connection, query: str, params: Any = None) -> Row:
    """Run an INSERT/UPDATE ... RETURNING * that must produce exactly one row."""
    row = fetch_one(conn, query, params)
    if row is None:
        raise LookupError("statement returned no row")
    return row


def money(value: Decimal) -> Decimal:
    """Refuse floats at the boundary: a float amount is a bug, not a rounding choice."""
    if not isinstance(value, Decimal):
        raise TypeError(f"money must be Decimal, got {type(value).__name__}")
    return value
