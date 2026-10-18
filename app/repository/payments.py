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
    method_name: str | None = None,
    method_type: str | None = None,
    tax_amount: Decimal = Decimal(0),
) -> Row:
    """`payment_method` is the one-word label; `method_name`/`method_type` the TMF
    paymentMethod's name and @type as sent (neither given: the label is the name).
    `amount` is the total received, `tax_amount` the part of it the client called tax
    (written only when not 0: tests insert payments into schemas before 0025)."""
    if method_name is None and method_type is None:
        method_name = payment_method
    row = returning(
        conn,
        """
        INSERT INTO payment
            (billing_account_id, currency, amount, received_at, payment_method, status,
             payment_method_name, payment_method_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            billing_account_id,
            currency,
            money(amount),
            received_at,
            payment_method,
            status,
            method_name,
            method_type,
        ),
    )
    if not tax_amount:
        return row
    return returning(
        conn,
        "UPDATE payment SET tax_amount = %s WHERE id = %s RETURNING *",
        (money(tax_amount), row["id"]),
    )


def get_payment(conn: psycopg.Connection, payment_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM payment WHERE id = %s", (payment_id,))


def list_payments(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "payment", where=where, offset=offset, limit=limit)
