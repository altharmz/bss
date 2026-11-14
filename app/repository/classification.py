"""TMF620 organization, serialized writes keep hierarchy and memberships consistent."""

from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository._sql import Row, fetch_all, fetch_one, returning, update_by_id

Table = Literal["category", "product_catalog", "product_offering"]
Membership = Literal["catalog", "offering"]


def lock(conn: psycopg.Connection) -> None:
    # Shared by category, catalog and offering membership writes. Distinct from worker locks.
    conn.execute("SELECT pg_advisory_xact_lock(620, 9)")


def get(conn: psycopg.Connection, table: Table, uid: UUID) -> Row | None:
    return fetch_one(
        conn, sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table)), (uid,)
    )


def create(conn: psycopg.Connection, table: Table, values: dict[str, Any]) -> Row:
    return returning(
        conn,
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        list(values.values()),
    )


def update(conn: psycopg.Connection, table: Table, uid: UUID, values: dict[str, Any]) -> Row:
    return update_by_id(conn, table, uid, values)


def related(conn: psycopg.Connection, table: Table, column: str, uid: UUID) -> list[Row]:
    return fetch_all(
        conn,
        sql.SQL("SELECT * FROM {} WHERE {} = %s ORDER BY created_at, id").format(
            sql.Identifier(table), sql.Identifier(column)
        ),
        (uid,),
    )


def memberships(
    conn: psycopg.Connection, kind: Membership, uid: UUID, *, reverse: bool = False
) -> list[Row]:
    owner = "catalog_id" if kind == "catalog" else "offering_id"
    target = "product_catalog" if kind == "catalog" else "product_offering"
    return fetch_all(
        conn,
        sql.SQL(
            "SELECT t.* FROM {} m JOIN {} t ON t.id = m.{} WHERE m.{} = %s"
            " ORDER BY m.created_at, t.id"
        ).format(
            sql.Identifier(f"{kind}_category"),
            sql.Identifier(target if reverse else "category"),
            sql.Identifier(owner if reverse else "category_id"),
            sql.Identifier("category_id" if reverse else owner),
        ),
        (uid,),
    )


def memberships_of(
    conn: psycopg.Connection, kind: Membership, uids: list[UUID]
) -> dict[UUID, list[Row]]:
    """`memberships` (the categories) of a page of catalogs or offerings in one query
    (#303), each list in the same `m.created_at, t.id` order."""
    if not uids:
        return {}
    owner = "catalog_id" if kind == "catalog" else "offering_id"
    groups: dict[UUID, list[Row]] = {uid: [] for uid in uids}
    rows = fetch_all(
        conn,
        sql.SQL(
            "SELECT m.{} AS member_of, t.* FROM {} m JOIN category t ON t.id = m.category_id"
            " WHERE m.{} = ANY(%s) ORDER BY m.created_at, t.id"
        ).format(sql.Identifier(owner), sql.Identifier(f"{kind}_category"), sql.Identifier(owner)),
        (list(set(uids)),),
    )
    for row in rows:
        groups[row.pop("member_of")].append(row)
    return groups


def replace(
    conn: psycopg.Connection, kind: Membership, uid: UUID, ids: list[UUID], *, reverse: bool = False
) -> None:
    owner = "catalog_id" if kind == "catalog" else "offering_id"
    column, other = ("category_id", owner) if reverse else (owner, "category_id")
    table = sql.Identifier(f"{kind}_category")
    conn.execute(
        sql.SQL("DELETE FROM {} WHERE {} = %s AND NOT ({} = ANY(%s::uuid[]))").format(
            table, sql.Identifier(column), sql.Identifier(other)
        ),
        (uid, ids),
    )
    for target in ids:
        conn.execute(
            sql.SQL("INSERT INTO {} ({}, {}) VALUES (%s, %s) ON CONFLICT DO NOTHING").format(
                table, sql.Identifier(column), sql.Identifier(other)
            ),
            (uid, target),
        )


def remove_memberships(conn: psycopg.Connection, kind: Membership, owner_id: UUID) -> None:
    """Drop every category membership of a catalog or offering (before it is deleted)."""
    owner = "catalog_id" if kind == "catalog" else "offering_id"
    conn.execute(
        sql.SQL("DELETE FROM {} WHERE {} = %s").format(
            sql.Identifier(f"{kind}_category"), sql.Identifier(owner)
        ),
        (owner_id,),
    )


def ancestors(conn: psycopg.Connection, uid: UUID) -> list[Row]:
    return fetch_all(
        conn,
        """
        WITH RECURSIVE chain AS (
            SELECT id, parent_id FROM category WHERE id = %s
            UNION SELECT c.id, c.parent_id FROM category c JOIN chain p ON c.id = p.parent_id
        ) SELECT id FROM chain
    """,
        (uid,),
    )
