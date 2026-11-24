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


# (database, table) -> {column: (data_type, udt_name)}; (database, enum) -> labels.
# Loaded once per process (and database: the tests use one per test).
_COLUMN_TYPES: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
_ENUM_LABELS: dict[tuple[str, str], frozenset[str]] = {}
TEXT_TYPES = {"text", "character varying", "character"}


def _column_types(conn: psycopg.Connection, table: str) -> dict[str, tuple[str, str]]:
    key = (conn.info.dbname, table)
    if key not in _COLUMN_TYPES:
        rows = conn.execute(
            "SELECT column_name, data_type, udt_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        ).fetchall()
        _COLUMN_TYPES[key] = {name: (data_type, udt) for name, data_type, udt in rows}
    return _COLUMN_TYPES[key]


def _enum_labels(conn: psycopg.Connection, enum: str) -> frozenset[str] | None:
    """The labels of an enum type; None when `enum` is not one."""
    key = (conn.info.dbname, enum)
    if key not in _ENUM_LABELS:
        rows = conn.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
            " WHERE t.typname = %s",
            (enum,),
        ).fetchall()
        _ENUM_LABELS[key] = frozenset(r[0] for r in rows)
    return _ENUM_LABELS[key] or None


def conditions(
    conn: psycopg.Connection, table: str, where: dict[str, Any]
) -> tuple[list[sql.Composable], list[Any]] | None:
    """The `column = value` conditions of a list filter on `table`, each compared with the
    column's own type so its indexes apply (#286, #311); None when a value can match no
    row (a string that is not a label of the column's enum), so the caller answers an
    empty page without a query. A value may also be IS_NULL or NOT_NULL, or a tuple of
    strings (listing.AnyOf: the column equals any of them). A string for a
    column of `table` that is neither text nor an enum keeps the `column::text` match; a
    column `table` does not have (a derived one) is compared as text.
    """
    conds, params = [], []
    types = None
    for column, value in where.items():
        ident = sql.Identifier(column)
        if value is IS_NULL:
            conds.append(sql.SQL("{} IS NULL").format(ident))
        elif value is NOT_NULL:
            conds.append(sql.SQL("{} IS NOT NULL").format(ident))
        elif isinstance(value, tuple):
            types = types if types is not None else _column_types(conn, table)
            data_type, udt = types.get(column, ("text", "text"))
            labels = _enum_labels(conn, udt) if data_type == "USER-DEFINED" else None
            members = [v for v in value if labels is None or v in labels]
            if not members:
                return None
            if labels is not None:
                cond = sql.SQL("{} = ANY(%s::{}[])").format(ident, sql.Identifier(udt))
            elif data_type in TEXT_TYPES:
                cond = sql.SQL("{} = ANY(%s)").format(ident)
            else:
                cond = sql.SQL("{}::text = ANY(%s)").format(ident)
            conds.append(cond)
            params.append(members)
        elif isinstance(value, str):
            types = types if types is not None else _column_types(conn, table)
            data_type, udt = types.get(column, ("text", "text"))
            labels = _enum_labels(conn, udt) if data_type == "USER-DEFINED" else None
            if labels is not None and value not in labels:
                return None
            typed = labels is not None or data_type in TEXT_TYPES
            conds.append(sql.SQL("{} = %s" if typed else "{}::text = %s").format(ident))
            params.append(value)
        else:
            conds.append(sql.SQL("{} = %s").format(ident))
            params.append(value)
    return conds, params


def select_page(
    conn: psycopg.Connection,
    table: str,
    *,
    where: dict[str, Any],
    offset: int,
    limit: int,
    newest_first: bool = False,
    extra: sql.Composable | None = None,
) -> tuple[list[Row], bool]:
    """One page of `table` matching every `column = value` in `where` (a value may also
    be IS_NULL or NOT_NULL), and whether more rows follow it: no total is counted (#312;
    it reads limit + 1 rows). Ordered by creation,
    so pages are stable (the `(created_at, id)` indexes of migration 0070 serve it, read
    backwards for `newest_first`).

    `table` and the column names are identifiers chosen by the caller's code, never user
    input. Values are compared with the column's own type, so the filter indexes apply
    (#286). A string for an enum column is checked against the enum's labels first: one
    that is not a label matches nothing, and the answer is an empty page with no query. A
    string for a column that is neither text nor an enum keeps the `column::text` match.
    `extra`: more select-list items after `*` (a correlated subquery, for example).
    """
    filters = conditions(conn, table, where)
    if filters is None:
        return [], False
    conds, params = filters
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds) if conds else sql.SQL("")
    ident = sql.Identifier(table)
    rows = fetch_all(
        conn,
        sql.SQL("SELECT *{} FROM {}").format(
            sql.SQL("") if extra is None else sql.SQL(", ") + extra, ident
        )
        + clause
        + sql.SQL(
            " ORDER BY created_at DESC, id DESC OFFSET %s LIMIT %s"
            if newest_first
            else " ORDER BY created_at, id OFFSET %s LIMIT %s"
        ),
        [*params, offset, limit + 1],
    )
    return rows[:limit], len(rows) > limit


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


def is_referenced(
    conn: psycopg.Connection, row_id: Any, sources: dict[str, tuple[str, str]]
) -> bool:
    """Whether any (table, column) of `sources` has a row with `column = row_id`: one
    EXISTS per source, stopping at the first. For guards that need no counts."""
    for table, column in sources.values():
        row = fetch_one(
            conn,
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {} WHERE {} = %s) AS found").format(
                sql.Identifier(table), sql.Identifier(column)
            ),
            (row_id,),
        )
        assert row is not None
        if row["found"]:
            return True
    return False


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
