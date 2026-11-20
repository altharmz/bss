"""Network provisioning commands of order items (docs/decisions/network.md).

One command per order item. Its id is the idempotency key sent to the network, derived
from the item and the kind, so a replay of acceptance or of a send names the same command.
"""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning

NAMESPACE = UUID("3b8d2c4e-7a15-5f06-9e31-c0a4d7b2e815")
# Waits after the 1st, 2nd, ... temporary error; beyond the table, the last one.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))


def pool_command_id(product_id: UUID, period_start: date, size_mb: int) -> UUID:
    """A dataPool command is one per member, pool period and pool size: the size never falls
    inside a period, so a size is told once."""
    return uuid5(NAMESPACE, f"dataPool:{product_id}:{period_start}:{size_mb}")


def command_id(order_item_id: UUID, kind: str, purpose: str = "order") -> UUID:
    key = f"{order_item_id}:{kind}" if purpose == "order" else f"{order_item_id}:{kind}:{purpose}"
    return uuid5(NAMESPACE, key)


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM network_command WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid,))


def insert(
    conn: psycopg.Connection,
    *,
    order_id: UUID,
    order_item_id: UUID,
    product_id: UUID | None,
    kind: str,
    due_date: date,
    profile: dict[str, Any],
    now: datetime,
    purpose: str = "order",
) -> Row:
    """`purpose` passEnd: the change_profile that ends a pass of the item before its plan
    period ends (docs/decisions/prepaid.md); one per item beside its order command."""
    return returning(
        conn,
        "INSERT INTO network_command (id, product_order_id, product_order_item_id, product_id,"
        " kind, due_date, profile, created_at, purpose)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (id) DO UPDATE SET id = EXCLUDED.id RETURNING *",
        (
            command_id(order_item_id, kind, purpose),
            order_id,
            order_item_id,
            product_id,
            kind,
            due_date,
            jsonb(profile),
            now,
            purpose,
        ),
    )


def insert_pool(
    conn: psycopg.Connection,
    *,
    product_id: UUID,
    due_date: date,
    profile: dict[str, Any],
    data_pool: dict[str, Any],
    now: datetime,
) -> Row | None:
    """A dataPool change_profile (no order): the member's profile with the pool's new size.
    None when it exists already."""
    return fetch_one(
        conn,
        "INSERT INTO network_command (id, product_id, kind, due_date, profile, created_at,"
        " purpose, data_pool) VALUES (%s, %s, 'change_profile', %s, %s, %s, 'dataPool', %s)"
        " ON CONFLICT (id) DO NOTHING RETURNING *",
        (
            pool_command_id(
                product_id, date.fromisoformat(data_pool["periodStart"]), data_pool["mb"]
            ),
            product_id,
            due_date,
            jsonb(profile),
            now,
            jsonb(data_pool),
        ),
    )


def pool_told(conn: psycopg.Connection, product_ids: list[UUID], today: date) -> dict[UUID, Row]:
    """What each line was last told, or is about to be told, of its pool: its newest
    activate or change_profile not cancelled or refused that carries pool fields, or that is
    due and not sent yet (it carries the pool when it is sent). `data_pool` null: unsent."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (product_id) product_id, id, state, data_pool FROM network_command"
        " WHERE product_id = ANY(%s::uuid[]) AND kind IN ('activate', 'change_profile')"
        " AND state NOT IN ('cancelled', 'refused')"
        " AND (data_pool IS NOT NULL OR (request IS NULL AND due_date <= %s))"
        " ORDER BY product_id, (request IS NULL) DESC, created_at DESC, id",
        (product_ids, today),
    )
    return {row["product_id"]: row for row in rows}


def activated(conn: psycopg.Connection, product_ids: list[UUID]) -> set[UUID]:
    """The lines the network confirmed an activate for."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT product_id FROM network_command WHERE product_id = ANY(%s::uuid[])"
        " AND kind = 'activate' AND state = 'confirmed'",
        (product_ids,),
    )
    return {row["product_id"] for row in rows}


def cancel_superseded_pool(conn: psycopg.Connection, product_id: UUID, keep: UUID) -> None:
    """An unsent dataPool command of the line other than `keep` names an old size."""
    conn.execute(
        "UPDATE network_command SET state = 'cancelled' WHERE product_id = %s"
        " AND purpose = 'dataPool' AND id <> %s AND state IN ('pending', 'exhausted')",
        (product_id, keep),
    )


def ended_passes(conn: psycopg.Connection, today: date) -> list[Row]:
    """Add-on passes that ended on or before `today` inside a paid period of their plan (not
    at its end, which needs no command), while the plan still runs, and whose pass-end
    change_profile was not made yet."""
    return fetch_all(
        conn,
        "SELECT a.id, a.parent_product_id, a.termination_date, a.created_by_item_id,"
        " i.product_order_id FROM product a"
        " JOIN product_order_item i ON i.id = a.created_by_item_id"
        " JOIN product p ON p.id = a.parent_product_id"
        " WHERE a.grantless AND a.termination_date <= %s"
        " AND a.status NOT IN ('cancelled', 'aborted')"
        " AND (p.termination_date IS NULL OR p.termination_date > a.termination_date)"
        " AND EXISTS (SELECT 1 FROM funded_interval f WHERE f.product_id = p.id"
        "  AND f.period @> a.termination_date AND lower(f.period) < a.termination_date)"
        " AND EXISTS (SELECT 1 FROM network_command n WHERE n.product_order_item_id = i.id"
        "  AND n.purpose = 'order' AND n.state = 'confirmed')"
        " AND NOT EXISTS (SELECT 1 FROM network_command n WHERE n.product_order_item_id = i.id"
        "  AND n.purpose = 'passEnd')"
        " ORDER BY a.termination_date, a.id",
        (today,),
    )


def of_order(conn: psycopg.Connection, order_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM network_command WHERE product_order_id = %s ORDER BY created_at, id",
        (order_id,),
    )


def due(conn: psycopg.Connection, today: date, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM network_command WHERE state = 'pending' AND due_date <= %s"
        " AND (next_attempt_at IS NULL OR next_attempt_at <= %s)"
        # an activation waits while its line's number is a port-in the carrier has not completed
        " AND NOT (kind = 'activate' AND product_id IS NOT NULL AND EXISTS ("
        "SELECT FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id"
        " WHERE a.product_id = network_command.product_id AND a.cancelled_at IS NULL"
        " AND n.origin = 'port_in' AND NOT EXISTS (SELECT FROM port_request p"
        " WHERE p.subscriber_number_id = n.id AND p.direction = 'in' AND p.state = 'completed')))"
        " ORDER BY due_date, created_at, id",
        (today, now),
    )


def freeze_request(
    conn: psycopg.Connection,
    uid: UUID,
    request: dict[str, Any],
    data_pool: dict[str, Any] | None = None,
) -> None:
    """The request as first sent; with it the pool it carried (a dataPool command has its
    own from the start)."""
    conn.execute(
        "UPDATE network_command SET request = %s, data_pool = coalesce(data_pool, %s)"
        " WHERE id = %s AND request IS NULL",
        (jsonb(request), None if data_pool is None else jsonb(data_pool), uid),
    )


def confirm(conn: psycopg.Connection, uid: UUID, *, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE network_command SET state = 'confirmed', confirmed_at = %s, attempted_at = %s,"
        " attempts = attempts + 1, last_error = NULL, next_attempt_at = NULL WHERE id = %s"
        " RETURNING *",
        (now, now, uid),
    )


def refuse(conn: psycopg.Connection, uid: UUID, *, code: str, reason: str, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE network_command SET state = 'refused', refusal_code = %s, last_error = %s,"
        " attempted_at = %s, attempts = attempts + 1, next_attempt_at = NULL WHERE id = %s"
        " RETURNING *",
        (code, reason, now, uid),
    )


def temporary(
    conn: psycopg.Connection, uid: UUID, *, error: str, max_attempts: int, now: datetime
) -> Row:
    """A temporary error: wait and send again, or `exhausted` once the attempts are used."""
    row = get(conn, uid, lock=True)
    attempts = row["attempts"] + 1
    if attempts >= max_attempts:
        return returning(
            conn,
            "UPDATE network_command SET state = 'exhausted', attempts = %s, last_error = %s,"
            " attempted_at = %s, next_attempt_at = NULL WHERE id = %s RETURNING *",
            (attempts, error, now, uid),
        )
    wait = BACKOFF[min(attempts, len(BACKOFF)) - 1]
    return returning(
        conn,
        "UPDATE network_command SET attempts = %s, last_error = %s, attempted_at = %s,"
        " next_attempt_at = %s WHERE id = %s RETURNING *",
        (attempts, error, now, now + wait, uid),
    )


def retry(conn: psycopg.Connection, uid: UUID) -> Row:
    """An exhausted command goes back to pending with its identity and request."""
    return returning(
        conn,
        "UPDATE network_command SET state = 'pending', attempts = 0, next_attempt_at = NULL"
        " WHERE id = %s AND state = 'exhausted' RETURNING *",
        (uid,),
    )


def cancel_open(conn: psycopg.Connection, order_id: UUID) -> None:
    conn.execute(
        "UPDATE network_command SET state = 'cancelled'"
        " WHERE product_order_id = %s AND state IN ('pending', 'exhausted')",
        (order_id,),
    )


def page(
    conn: psycopg.Connection,
    *,
    order_id: UUID | None,
    product_id: UUID | None,
    state: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    where = (  # noqa: S608 - fixed text, the values are parameters
        " WHERE (%(o)s::uuid IS NULL OR product_order_id = %(o)s)"
        " AND (%(p)s::uuid IS NULL OR product_id = %(p)s)"
        " AND (%(s)s::text IS NULL OR state = %(s)s)"
    )
    args = {"o": order_id, "p": product_id, "s": state, "off": offset, "lim": limit + 1}
    rows = fetch_all(
        conn,
        f"SELECT * FROM network_command{where} ORDER BY created_at DESC, id"  # noqa: S608
        " LIMIT %(lim)s OFFSET %(off)s",
        args,
    )
    return rows[:limit], len(rows) > limit
