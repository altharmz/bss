from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
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


def select_page(
    conn: psycopg.Connection,
    table: str,
    *,
    where: dict[str, Any],
    offset: int,
    limit: int,
) -> tuple[list[Row], int]:
    """One page of `table` matching every `column = value` in `where`, plus the total
    number of matching rows. Ordered by creation, so pages are stable.

    `table` and the column names are identifiers chosen by the caller's code, never user
    input. String values are compared with `column::text`, so a value that is not a member
    of an enum column matches nothing instead of raising.
    """
    conds = [
        sql.SQL("{}::text = %s" if isinstance(value, str) else "{} = %s").format(
            sql.Identifier(column)
        )
        for column, value in where.items()
    ]
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds) if conds else sql.SQL("")
    params = list(where.values())
    ident = sql.Identifier(table)
    total = fetch_one(conn, sql.SQL("SELECT count(*) AS n FROM {}").format(ident) + clause, params)
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM {}").format(ident)
        + clause
        + sql.SQL(" ORDER BY created_at, id OFFSET %s LIMIT %s"),
        [*params, offset, limit],
    )
    assert total is not None
    return rows, total["n"]


def update_by_id(conn: psycopg.Connection, table: str, row_id: Any, changes: dict[str, Any]) -> Row:
    """Set the given columns on one row and return it. Raises LookupError if it is gone."""
    if not changes:
        row = fetch_one(
            conn, sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table)), (row_id,)
        )
        if row is None:
            raise LookupError("statement returned no row")
        return row
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
    )
    return returning(
        conn,
        sql.SQL("UPDATE {} SET ").format(sql.Identifier(table))
        + assignments
        + sql.SQL(" WHERE id = %s RETURNING *"),
        [*changes.values(), row_id],
    )
