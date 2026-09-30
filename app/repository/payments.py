"""Payments (TMF676 v4)."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_one, money, returning, select_page


def insert_payment(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID,
    amount: Decimal,
    currency: str,
    received_at: datetime,
    payment_method: str,
    status: str = "done",
) -> Row:
    return returning(
        conn,
        """
        INSERT INTO payment
            (billing_account_id, currency, amount, received_at, payment_method, status)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (billing_account_id, currency, money(amount), received_at, payment_method, status),
    )


def get_payment(conn: psycopg.Connection, payment_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM payment WHERE id = %s", (payment_id,))


def list_payments(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "payment", where=where, offset=offset, limit=limit)
