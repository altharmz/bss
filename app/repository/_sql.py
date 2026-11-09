from decimal import Decimal
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

Row = dict[str, Any]


def jsonb(value: dict[str, Any]) -> Jsonb:
    """A document for a jsonb column; tmfjson: numbers stay exact decimals, never floats."""
    from app.api import tmfjson  # lazily: importing app.api loads every API module

    return Jsonb(value, dumps=tmfjson.dumps)


def jsonb_list(value: list[Any]) -> Jsonb:
    """A JSON array for a jsonb column."""
    return Jsonb(value)


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


Lock = Literal["key share", "update"] | None
LOCK_CLAUSE = {None: "", "key share": " FOR KEY SHARE", "update": " FOR UPDATE"}


def select_by_id(
    conn: psycopg.Connection, table: str, row_id: Any, lock: Lock = None
) -> Row | None:
    """One row by id. `lock`: "key share" for a writer that is about to reference the row
    (a delete, which locks "update", waits for it), "update" for the delete itself or a
    writer that changes the row anyway."""
    query = sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table))
    return fetch_one(conn, query + sql.SQL(LOCK_CLAUSE[lock]), (row_id,))


def rows_by_id(conn: psycopg.Connection, table: str, ids: list[Any]) -> dict[Any, Row]:
    """Rows of `table` by id, for a whole page in one query (#285)."""
    if not ids:
        return {}
    query = sql.SQL("SELECT * FROM {} WHERE id = ANY(%s)").format(sql.Identifier(table))
    return {row["id"]: row for row in fetch_all(conn, query, (list(set(ids)),))}


def rows_grouped_by(
    conn: psycopg.Connection, table: str, column: str, keys: list[Any], order: str
) -> dict[Any, list[Row]]:
    """Rows of `table` whose `column` is one of `keys`, grouped by it, each group in
    `order` (the single-row query's ORDER BY, so nested arrays keep their order): one
    query for a whole page (#285). `table`, `column` and `order` come from code."""
    if not keys:
        return {}
    query = sql.SQL("SELECT * FROM {} WHERE {} = ANY(%s) ORDER BY " + order).format(  # noqa: S608 - order is code
        sql.Identifier(table), sql.Identifier(column)
    )
    groups: dict[Any, list[Row]] = {key: [] for key in keys}
    for row in fetch_all(conn, query, (list(set(keys)),)):
        groups[row[column]].append(row)
    return groups


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
