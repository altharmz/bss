"""The one helper of the performance guards (#440): the plan of a statement, the statements
a module sends, and the statements a block runs. EXPLAIN output belongs to the planner, so a
Postgres upgrade that changes it is fixed here, not in every guard. docs/decisions/harness.md,
"Test layout"."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

# The test tables are empty, so a sequential scan (and a bitmap scan with a sort of nothing)
# looks as cheap as the index; turned off, the planner must choose an index that can serve
# the statement. INDEX_ORDER also wants the index read in order.
NO_SEQSCAN = ("seqscan",)
INDEX_ORDER = ("seqscan", "bitmapscan")


def plan(conn, query, params=(), *, off=INDEX_ORDER, json=False):
    """The EXPLAIN of `query` with the scans in `off` turned off for the call: the text, or
    with `json` the root node of the JSON plan."""
    for scan in off:
        conn.execute(f"SET enable_{scan} = off")
    try:
        if json:
            return conn.execute("EXPLAIN (FORMAT JSON) " + query, params).fetchone()[0][0]["Plan"]
        return "\n".join(r[0] for r in conn.execute("EXPLAIN " + query, params))
    finally:
        for scan in off:
            conn.execute(f"RESET enable_{scan}")


def sent(monkeypatch, module, function="fetch_all", *, needle=None, run=True) -> list[tuple]:
    """The (query text, params) of the statements `module.function(conn, query, params)`
    sends while the test runs, those whose text has `needle` if given. With `run` false the
    statements return no rows instead of running."""
    real = getattr(module, function)
    seen: list[tuple] = []

    def recording(conn, query, params=None):
        text = query.as_string(conn) if hasattr(query, "as_string") else query
        if needle is None or needle in text:
            seen.append((text, params))
        return real(conn, query, params) if run else []

    monkeypatch.setattr(module, function, recording)
    return seen


@contextmanager
def counting(monkeypatch) -> Iterator[list[str]]:
    """Every statement executed through psycopg while the block runs (connection- and
    cursor-level execute, the app's pool included): list endpoints must cost a constant
    number of statements, whatever the page size (#285)."""
    seen: list[str] = []
    real_cursor = psycopg.Cursor.execute
    real_conn = psycopg.Connection.execute

    def cursor_execute(self, query, *args, **kwargs):
        seen.append(str(query))
        return real_cursor(self, query, *args, **kwargs)

    def conn_execute(self, query, *args, **kwargs):
        # Connection.execute runs through a cursor's execute: counted there
        return real_conn(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", cursor_execute)
    monkeypatch.setattr(psycopg.Connection, "execute", conn_execute)
    try:
        yield seen
    finally:
        monkeypatch.setattr(psycopg.Cursor, "execute", real_cursor)
        monkeypatch.setattr(psycopg.Connection, "execute", real_conn)


def statements(api, monkeypatch, url: str) -> int:
    """The statements of one request, through the raw client: the contract-checking client
    fetches schemas with requests of its own."""
    with counting(monkeypatch) as seen:
        response = api.client.get(url)
    assert response.status_code == 200, response.text
    return len(seen)
