"""The runtime log level and its change audit (docs/decisions/infra.md, "Logging")."""

from datetime import datetime

import psycopg

from app.repository._sql import Row, fetch_one, returning


def get_row(conn: psycopg.Connection, *, lock: bool = False) -> Row:
    query = "SELECT * FROM log_setting"
    row = fetch_one(conn, query + (" FOR UPDATE" if lock else ""))
    assert row is not None  # the migration inserts the row
    return row


def update(
    conn: psycopg.Connection,
    *,
    base_level: str | None,
    debug_until: datetime | None,
    operator: str,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "UPDATE log_setting SET base_level = %s, debug_until = %s, updated_at = %s,"
        " updated_by = %s RETURNING *",
        (base_level, debug_until, now, operator),
    )


def append_change(conn: psycopg.Connection, *, operator: str, old: Row, new: Row, now: datetime):
    conn.execute(
        "INSERT INTO log_setting_change (operator, old_base_level, new_base_level,"
        " old_debug_until, new_debug_until, changed_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            operator,
            old["base_level"],
            new["base_level"],
            old["debug_until"],
            new["debug_until"],
            now,
        ),
    )
