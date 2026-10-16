"""TMF666 financial accounts (and the billing accounts linked to them)."""

from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository import billing
from app.repository._sql import Row, fetch_all, fetch_one, returning, select_page, update_by_id

FINANCIAL = "financial_account"


def get_financial(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    """`lock`: FOR SHARE, so a concurrent state change waits until a new link commits."""
    query = "SELECT * FROM financial_account WHERE id = %s"
    return fetch_one(conn, query + " FOR SHARE" if lock else query, (uid,))


def page_financial(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, FINANCIAL, where=where, offset=offset, limit=limit)


def create_financial(conn: psycopg.Connection, values: dict[str, Any]) -> Row:
    return returning(
        conn,
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(FINANCIAL),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() * len(values)),
        ),
        list(values.values()),
    )


def update_financial(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, FINANCIAL, uid, changes)


def linked_billing_accounts(conn: psycopg.Connection, uid: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM billing_account WHERE financial_account_id = %s ORDER BY created_at, id",
        (uid,),
    )


def balances(conn: psycopg.Connection, uid: UUID) -> dict[str, Decimal]:
    """currency -> the sum of the linked billing accounts' balances, by currency."""
    totals: dict[str, Decimal] = {}
    for account in linked_billing_accounts(conn, uid):
        balance = billing.account_balance(conn, account["id"])
        totals[account["currency"]] = totals.get(account["currency"], Decimal(0)) + balance
    return dict(sorted(totals.items()))
