"""TMF666 billing cycle specifications (migration 0019): named instances of the one
monthly policy. The built-in `monthly` is not stored."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository._sql import Lock, Row, fetch_all, returning, select_by_id, update_by_id

TABLE = "billing_cycle_specification"


def get(conn: psycopg.Connection, uid: UUID, lock: Lock = None) -> Row | None:
    return select_by_id(conn, TABLE, uid, lock)


def all_rows(conn: psycopg.Connection) -> list[Row]:
    return fetch_all(conn, "SELECT * FROM billing_cycle_specification ORDER BY created_at, id")


def create(conn: psycopg.Connection, values: dict[str, Any]) -> Row:
    return returning(
        conn,
        sql.SQL("INSERT INTO billing_cycle_specification ({}) VALUES ({}) RETURNING *").format(
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() * len(values)),
        ),
        list(values.values()),
    )


def account_count(conn: psycopg.Connection, uid: UUID) -> int:
    rows = fetch_all(
        conn, "SELECT id FROM billing_account WHERE cycle_specification_id = %s", (uid,)
    )
    return len(rows)


def update(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, TABLE, uid, changes)
