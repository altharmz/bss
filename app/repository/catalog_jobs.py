"""TMF620 import and export jobs (one table, `kind` = the resource's @type)."""

from typing import Any, Literal
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_one, returning, select_page

Kind = Literal["ImportJob", "ExportJob"]


def insert(conn: psycopg.Connection, values: dict[str, Any]) -> Row:
    """`values["id"]` None: a new one (a client may choose it)."""
    return returning(
        conn,
        """
        INSERT INTO catalog_job
            (id, kind, status, content_type, path, query, url, error_log,
             creation_date, completion_date)
        VALUES (COALESCE(%(id)s::uuid, gen_random_uuid()), %(kind)s, %(status)s,
                %(content_type)s, %(path)s, %(query)s, %(url)s,
                %(error_log)s, %(creation_date)s, %(completion_date)s)
        RETURNING *
        """,
        values,
    )


def get(conn: psycopg.Connection, kind: Kind, job_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM catalog_job WHERE id = %s AND kind = %s", (job_id, kind))


def page(
    conn: psycopg.Connection, kind: Kind, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(
        conn, "catalog_job", where={"kind": kind, **where}, offset=offset, limit=limit
    )


def delete(conn: psycopg.Connection, kind: Kind, job_id: UUID) -> bool:
    row = fetch_one(
        conn,
        "DELETE FROM catalog_job WHERE id = %s AND kind = %s RETURNING id",
        (job_id, kind),
    )
    return row is not None
