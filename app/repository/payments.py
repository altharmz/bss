"""Payments (TMF676 v4)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import psycopg

from app.repository._sql import Row, money, returning


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
