"""Barring and lifting of pay-as-you-go lines (docs/decisions/usage.md, "Barring").

One `usage_bar` row per command, numbered per product; the id is the idempotency key sent to
the network and derives from the product and the number, so a replay names the same command.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning
from app.repository.network_commands import BACKOFF

NAMESPACE = UUID("9d5e1a7c-24b8-5c3f-8e60-3f1b7a9c4d52")


def bar_id(product_id: UUID, seq: int) -> UUID:
    return uuid5(NAMESPACE, f"{product_id}:{seq}")


def payg_lines(conn: psycopg.Connection, account_id: UUID, today: date) -> list[Row]:
    """The account's pay-as-you-go candidates (grantless, no plan parent, running today):
    the caller keeps those whose offering is `payAsYouGo`."""
    return fetch_all(
        conn,
        "SELECT p.id, p.status, p.manually_suspended, o.details FROM product p"
        " JOIN product_offering o ON o.id = product_offering_on(p.id, %s)"
        " WHERE p.billing_account_id = %s AND p.grantless AND p.parent_product_id IS NULL"
        " AND p.status NOT IN ('terminated', 'cancelled', 'aborted', 'created')"
        " AND p.start_date <= %s AND (p.termination_date IS NULL OR p.termination_date > %s)"
        " ORDER BY p.id",
        (today, account_id, today, today),
    )


def accounts_with_lines(conn: psycopg.Connection) -> list[UUID]:
    rows = fetch_all(
        conn,
        "SELECT DISTINCT p.billing_account_id FROM product p"
        " JOIN billing_account a ON a.id = p.billing_account_id"
        " WHERE p.grantless AND p.parent_product_id IS NULL AND a.rating_type = 'prepaid'"
        " AND p.status = 'active' ORDER BY p.billing_account_id",
    )
    return [row["billing_account_id"] for row in rows]


def latest(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    """The line's latest command, which is its barring state (a cancelled one never took
    effect and does not count); the highest number overall is `last_seq`."""
    return fetch_one(
        conn,
        "SELECT * FROM usage_bar WHERE product_id = %s AND state <> 'cancelled'"
        " ORDER BY seq DESC LIMIT 1",
        (product_id,),
    )


def create(conn: psycopg.Connection, product_id: UUID, kind: str, now: datetime) -> Row:
    row = fetch_one(
        conn,
        "SELECT coalesce(max(seq), 0) AS n FROM usage_bar WHERE product_id = %s",
        (product_id,),
    )
    assert row is not None
    seq = row["n"] + 1
    return returning(
        conn,
        "INSERT INTO usage_bar (id, product_id, seq, kind, created_at)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (bar_id(product_id, seq), product_id, seq, kind, now),
    )


def due(conn: psycopg.Connection, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM usage_bar WHERE state = 'pending'"
        " AND (next_attempt_at IS NULL OR next_attempt_at <= %s) ORDER BY created_at, id",
        (now,),
    )


def get(conn: psycopg.Connection, bar: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM usage_bar WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (bar,))


def freeze_request(conn: psycopg.Connection, bar: UUID, request: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE usage_bar SET request = %s WHERE id = %s AND request IS NULL",
        (jsonb(request), bar),
    )


def confirm(conn: psycopg.Connection, bar: UUID, *, now: datetime) -> None:
    conn.execute(
        "UPDATE usage_bar SET state = 'confirmed', confirmed_at = %s, attempts = attempts + 1,"
        " next_attempt_at = NULL, last_error = NULL WHERE id = %s",
        (now, bar),
    )


def refuse(conn: psycopg.Connection, bar: UUID, *, code: str, reason: str) -> None:
    conn.execute(
        "UPDATE usage_bar SET state = 'refused', refusal_code = %s, last_error = %s,"
        " attempts = attempts + 1, next_attempt_at = NULL WHERE id = %s",
        (code, reason, bar),
    )


def temporary(conn: psycopg.Connection, bar: Row, *, error: str, now: datetime) -> None:
    """A temporary error: the same command is sent again after the backoff (never
    exhausted: a line stays barred or served until the network answers)."""
    wait = BACKOFF[min(bar["attempts"], len(BACKOFF) - 1)]
    conn.execute(
        "UPDATE usage_bar SET attempts = attempts + 1, last_error = %s, next_attempt_at = %s"
        " WHERE id = %s",
        (error, now + wait, bar["id"]),
    )


def cancel(conn: psycopg.Connection, bar: UUID) -> None:
    conn.execute("UPDATE usage_bar SET state = 'cancelled' WHERE id = %s", (bar,))


def held(conn: psycopg.Connection, account_id: UUID) -> Decimal:
    row = fetch_one(
        conn,
        "SELECT coalesce(sum(amount), 0) AS held FROM funding_hold"
        " WHERE billing_account_id = %s AND released_at IS NULL",
        (account_id,),
    )
    assert row is not None
    return row["held"]
