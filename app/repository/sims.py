"""SIM and eSIM stock on the IMSI resources (docs/decisions/sim.md).

A SIM is the `subscriber_number` row of kind imsi. Its `sim_state` follows the order
that claims it: in_stock -> reserved (claimed, the network has not confirmed) -> active,
and retired once released. Callers hold the product locks before the number locks.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one

TYPES = ("physical", "esim")
STATES = ("in_stock", "reserved", "active", "retired")


def create_batch(conn: psycopg.Connection, name: str, sim_type: str, source: str, now: datetime):
    conn.execute(
        "INSERT INTO sim_batch (name, sim_type, source, created_at) VALUES (%s, %s, %s, %s)",
        (name, sim_type, source, now),
    )


def batch_exists(conn: psycopg.Connection, name: str) -> bool:
    return fetch_one(conn, "SELECT 1 FROM sim_batch WHERE name = %s", (name,)) is not None


def batches(conn: psycopg.Connection) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT b.*, coalesce(jsonb_object_agg(c.sim_state, c.n) FILTER (WHERE c.n IS NOT NULL),"
        " '{}'::jsonb) AS counts FROM sim_batch b LEFT JOIN ("
        " SELECT sim_batch, sim_state, count(*) AS n FROM subscriber_number"
        " WHERE sim_batch IS NOT NULL GROUP BY sim_batch, sim_state) c ON c.sim_batch = b.name"
        " GROUP BY b.name ORDER BY b.created_at DESC, b.name",
    )


def batch(conn: psycopg.Connection, name: str) -> Row | None:
    return next((row for row in batches(conn) if row["name"] == name), None)


def existing(conn: psycopg.Connection, imsis: list[str], iccids: list[str]) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT value, iccid FROM subscriber_number WHERE resource_specification_id = 'imsi'"
        " AND (value = ANY(%s) OR iccid = ANY(%s))",
        (imsis, iccids),
    )


def insert_many(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO subscriber_number (name, value, resource_specification_id, iccid,"
            " sim_type, sim_batch, esim_smdp_address, esim_matching_id, administrative_state,"
            " created_at, updated_at) VALUES (%(name)s, %(value)s, 'imsi', %(iccid)s,"
            " %(sim_type)s, %(batch)s, %(smdp)s, %(matching)s, 'unlocked', %(now)s, %(now)s)",
            rows,
        )


def pick_in_stock(conn: psycopg.Connection, sim_type: str, *, exclude=()) -> Row | None:
    """The oldest unlocked SIM of the type in stock, locked; skips rows other orders hold."""
    return fetch_one(
        conn,
        "SELECT * FROM subscriber_number WHERE resource_specification_id = 'imsi'"
        " AND sim_type = %s AND sim_state = 'in_stock' AND administrative_state = 'unlocked'"
        " AND NOT (id = ANY(%s)) ORDER BY created_at, value, id LIMIT 1 FOR UPDATE SKIP LOCKED",
        (sim_type, list(exclude)),
    )


def hold(conn: psycopg.Connection, sim_id: UUID, product_id: UUID, state: str, now) -> None:
    conn.execute(
        "UPDATE subscriber_number SET sim_state = %s, sim_product_id = %s, updated_at = %s"
        " WHERE id = %s AND resource_specification_id = 'imsi'",
        (state, product_id, now, sim_id),
    )


def release_hold(conn: psycopg.Connection, sim_id: UUID, now) -> None:
    """A claim that never took effect: back in stock (a retired SIM stays retired)."""
    conn.execute(
        "UPDATE subscriber_number SET sim_state = 'in_stock', sim_product_id = NULL,"
        " updated_at = %s WHERE id = %s AND sim_state IN ('reserved', 'active')",
        (now, sim_id),
    )


def retire(conn: psycopg.Connection, sim_id: UUID, now) -> None:
    conn.execute(
        "UPDATE subscriber_number SET sim_state = 'retired', updated_at = %s"
        " WHERE id = %s AND sim_state <> 'retired'",
        (now, sim_id),
    )


def reserved_of(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM subscriber_number WHERE sim_product_id = %s AND sim_state = 'reserved'",
        (product_id,),
    )


def activate_reserved(conn: psycopg.Connection, product_id: UUID, now) -> None:
    conn.execute(
        "UPDATE subscriber_number SET sim_state = 'active', updated_at = %s"
        " WHERE sim_product_id = %s AND sim_state = 'reserved'",
        (now, product_id),
    )


def reserve_claimed(conn: psycopg.Connection, product_id: UUID, now) -> None:
    """An activation waiting for the network: the product's active SIM is only reserved."""
    conn.execute(
        "UPDATE subscriber_number SET sim_state = 'reserved', updated_at = %s"
        " WHERE sim_product_id = %s AND sim_state = 'active'",
        (now, product_id),
    )


def unlock_state(conn: psycopg.Connection, sim_id: UUID, today, now) -> None:
    """An operator unlocked a retired SIM: back in stock unless a claim still runs."""
    conn.execute(
        "UPDATE subscriber_number n SET sim_state = 'in_stock', sim_product_id = NULL,"
        " updated_at = %s WHERE n.id = %s AND n.sim_state = 'retired' AND NOT EXISTS ("
        "SELECT 1 FROM number_assignment a WHERE a.subscriber_number_id = n.id"
        " AND a.cancelled_at IS NULL AND NOT isempty(a.valid_for)"
        " AND (a.released_on IS NULL OR a.released_on > %s))",
        (now, sim_id, today),
    )
