from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

Row = dict[str, Any]

# Filter values for select_page: `column IS NULL` / `column IS NOT NULL`.
IS_NULL = object()
NOT_NULL = object()


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
    """One page of `table` matching every `column = value` in `where` (a value may also
    be IS_NULL or NOT_NULL), plus the total number of matching rows. Ordered by creation,
    so pages are stable.

    `table` and the column names are identifiers chosen by the caller's code, never user
    input. String values are compared with `column::text`, so a value that is not a member
    of an enum column matches nothing instead of raising.
    """
    conds, params = [], []
    for column, value in where.items():
        ident = sql.Identifier(column)
        if value is IS_NULL:
            conds.append(sql.SQL("{} IS NULL").format(ident))
        elif value is NOT_NULL:
            conds.append(sql.SQL("{} IS NOT NULL").format(ident))
        else:
            template = "{}::text = %s" if isinstance(value, str) else "{} = %s"
            conds.append(sql.SQL(template).format(ident))
            params.append(value)
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds) if conds else sql.SQL("")
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


def count_references(
    conn: psycopg.Connection, row_id: Any, sources: dict[str, tuple[str, str]]
) -> dict[str, int]:
    """For each label -> (table, column): how many rows of `table` have `column = row_id`.
    Labels nothing refers through are left out, so an empty result means unreferenced."""
    counts = {}
    for label, (table, column) in sources.items():
        row = fetch_one(
            conn,
            sql.SQL("SELECT count(*) AS n FROM {} WHERE {} = %s").format(
                sql.Identifier(table), sql.Identifier(column)
            ),
            (row_id,),
        )
        assert row is not None
        if row["n"]:
            counts[label] = row["n"]
    return counts


def delete_by_id(conn: psycopg.Connection, table: str, row_id: Any) -> bool:
    """Delete one row; False if it was not there."""
    query = sql.SQL("DELETE FROM {} WHERE id = %s RETURNING id").format(sql.Identifier(table))
    return fetch_one(conn, query, (row_id,)) is not None


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
