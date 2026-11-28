"""Product offering qualifications (TMF679), stored as answered (migration 0092,
docs/decisions/qualification.md). A row is written once by POST; PATCH changes only its
details (what the client sent that has no behaviour); DELETE removes it, and so does the
worker's housekeeping 30 days after its creation (app/repository/housekeeping.py)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from app.repository._sql import (
    Row,
    delete_by_id,
    fetch_all,
    jsonb,
    returning,
    select_by_id,
    select_page,
    update_by_id,
)

TABLE = "product_offering_qualification"
KINDS = ("check", "query")


def _items(items: list[dict[str, Any]]) -> Jsonb:
    """The items as a jsonb array, written as the API answers them (tmfjson)."""
    from app.api import tmfjson  # lazily, as _sql.jsonb

    return Jsonb(items, dumps=tmfjson.dumps)


def insert(
    conn: psycopg.Connection,
    *,
    uid: UUID | None,
    kind: str,
    billing_account_id: UUID,
    qualification_result: str | None,
    qualification_date: date,
    now: datetime,
    expires_at: datetime,
    items: list[dict[str, Any]],
    details: dict[str, Any],
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO product_offering_qualification (id, kind, billing_account_id, state,
            qualification_result, qualification_date, created_at, expires_at, updated_at,
            items, details)
        VALUES (%s, %s, %s, 'done', %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            uid or uuid4(),
            kind,
            billing_account_id,
            qualification_result,
            qualification_date,
            now,
            expires_at,
            now,
            _items(items),
            jsonb(details),
        ),
    )


def get(conn: psycopg.Connection, kind: str, uid: UUID, *, lock: bool = False) -> Row | None:
    row = select_by_id(conn, TABLE, uid, "update" if lock else None)
    return row if row is not None and row["kind"] == kind else None


def page(
    conn: psycopg.Connection, kind: str, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    """Newest first."""
    return select_page(
        conn, TABLE, where={"kind": kind, **where}, offset=offset, limit=limit, newest_first=True
    )


def update(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    if "details" in changes:
        changes = changes | {"details": jsonb(changes["details"])}
    return update_by_id(conn, TABLE, uid, changes)


def delete(conn: psycopg.Connection, uid: UUID) -> bool:
    return delete_by_id(conn, TABLE, uid)


def sellable_offerings(conn: psycopg.Connection, limit: int) -> list[UUID]:
    """The offerings a query qualifies: Active and sellable (any other is refused by the
    order engine anyway), by name. At most `limit`."""
    rows = fetch_all(
        conn,
        "SELECT id FROM product_offering WHERE is_sellable AND lifecycle_status = 'Active'"
        " ORDER BY name, id LIMIT %s",
        (limit,),
    )
    return [row["id"] for row in rows]
