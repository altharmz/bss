"""Counting the SQL statements a request runs (#285): list endpoints must cost a constant
number of queries, whatever the page size."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg


@contextmanager
def counting(monkeypatch) -> Iterator[list[str]]:
    """Every statement executed through psycopg while the block runs (connection- and
    cursor-level execute, the app's pool included)."""
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
