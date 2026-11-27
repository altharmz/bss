"""The devices of prepaid lines (docs/decisions/network.md#device-issue-194).

One row per device a product has had; the row without `ended_at` is the current one. Rows
are never rewritten, a device change closes the current row and inserts the next.
"""

from datetime import datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, jsonb_list, returning


def history(conn: psycopg.Connection, product_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM product_device WHERE product_id = %s"
        " ORDER BY started_at DESC, (ended_at IS NULL) DESC, id",
        (product_id,),
    )


def replace(
    conn: psycopg.Connection,
    *,
    product_id: UUID,
    imei: str,
    compatibility: str,
    reasons: list[str],
    checked_at: datetime,
    item_id: UUID,
    now: datetime,
) -> Row:
    """Close the current device (if any) and record this one as the current."""
    conn.execute(
        "UPDATE product_device SET ended_at = %s, ended_by_item_id = %s"
        " WHERE product_id = %s AND ended_at IS NULL",
        (now, item_id, product_id),
    )
    return returning(
        conn,
        "INSERT INTO product_device (product_id, imei, compatibility, reasons, checked_at,"
        " started_at, recorded_by_item_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (product_id, imei, compatibility, jsonb_list(reasons), checked_at, now, item_id),
    )
