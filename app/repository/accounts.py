"""TMF666 financial accounts (and the accounts linked to them), party and settlement
accounts (one table, `kind`)."""

from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository import billing
from app.repository._sql import (
    Row,
    fetch_all,
    fetch_one,
    returning,
    rows_grouped_by,
    select_page,
    update_by_id,
)
from app.repository.parties import with_details

FINANCIAL = "financial_account"


def get_financial(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    """`lock`: FOR SHARE, so a concurrent state change waits until a new link commits."""
    query = "SELECT * FROM financial_account WHERE id = %s"
    return fetch_one(conn, query + " FOR SHARE" if lock else query, (uid,))


def page_financial(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    return select_page(conn, FINANCIAL, where=where, offset=offset, limit=limit)


def _insert(conn: psycopg.Connection, table: str, values: dict[str, Any]) -> Row:
    return returning(
        conn,
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() * len(values)),
        ),
        list(values.values()),
    )


def create_financial(conn: psycopg.Connection, values: dict[str, Any]) -> Row:
    return _insert(conn, FINANCIAL, with_details(values))


def update_financial(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, FINANCIAL, uid, with_details(changes))


def get_party_account(
    conn: psycopg.Connection, kind: str, uid: UUID, *, lock: bool = False
) -> Row | None:
    """`lock`: FOR UPDATE, so two changes of the owner and the link do not interleave."""
    query = "SELECT * FROM party_account WHERE id = %s AND kind = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid, kind))


def page_party_accounts(
    conn: psycopg.Connection, kind: str, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    return select_page(
        conn, "party_account", where={**where, "kind": kind}, offset=offset, limit=limit
    )


def create_party_account(conn: psycopg.Connection, values: dict[str, Any]) -> Row:
    return _insert(conn, "party_account", with_details(values))


def update_party_account(conn: psycopg.Connection, uid: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, "party_account", uid, with_details(changes))


def count_party_accounts(conn: psycopg.Connection, column: str, uid: UUID) -> dict[str, int]:
    """kind -> how many party/settlement accounts have `column = uid` (kinds with none
    left out). `column` is chosen by the caller's code: party_id or financial_account_id."""
    rows = fetch_all(
        conn,
        sql.SQL(
            "SELECT kind::text AS kind, count(*) AS n FROM party_account WHERE {} = %s"
            " GROUP BY kind ORDER BY kind"
        ).format(sql.Identifier(column)),
        (uid,),
    )
    return {row["kind"]: row["n"] for row in rows}


def linked_accounts(conn: psycopg.Connection, uid: UUID) -> list[Row]:
    """Every account a financial account contains, in creation order: billing accounts
    (`kind` BillingAccount) and party/settlement accounts. Only id, name, kind."""
    return fetch_all(
        conn,
        """
        SELECT id, name, kind, created_at FROM (
            SELECT id, name, 'BillingAccount' AS kind, created_at FROM billing_account
            WHERE financial_account_id = %(id)s
            UNION ALL
            SELECT id, name, kind::text, created_at FROM party_account
            WHERE financial_account_id = %(id)s
        ) linked ORDER BY created_at, id
        """,
        {"id": uid},
    )


def linked_accounts_of(conn: psycopg.Connection, uids: list[UUID]) -> dict[UUID, list[Row]]:
    """`linked_accounts` of a page of financial accounts in one query (#303), each list in
    the same creation order."""
    if not uids:
        return {}
    groups: dict[UUID, list[Row]] = {uid: [] for uid in uids}
    rows = fetch_all(
        conn,
        """
        SELECT financial_account_id, id, name, kind, created_at FROM (
            SELECT financial_account_id, id, name, 'BillingAccount' AS kind, created_at
            FROM billing_account WHERE financial_account_id = ANY(%(ids)s)
            UNION ALL
            SELECT financial_account_id, id, name, kind::text, created_at FROM party_account
            WHERE financial_account_id = ANY(%(ids)s)
        ) linked ORDER BY created_at, id
        """,
        {"ids": list(set(uids))},
    )
    for row in rows:
        groups[row.pop("financial_account_id")].append(row)
    return groups


def balances_of(conn: psycopg.Connection, uids: list[UUID]) -> dict[UUID, dict[str, Decimal]]:
    """financial account -> currency -> the sum of its linked billing accounts' balances,
    for a page: the accounts and their balances are each read in one query (#303)."""
    linked = rows_grouped_by(
        conn, "billing_account", "financial_account_id", uids, "created_at, id"
    )
    amounts = billing.account_balances(conn, [a["id"] for group in linked.values() for a in group])
    result: dict[UUID, dict[str, Decimal]] = {}
    for uid in uids:
        totals: dict[str, Decimal] = {}
        for account in linked.get(uid, []):
            currency = account["currency"]
            totals[currency] = totals.get(currency, Decimal(0)) + amounts[account["id"]]
        result[uid] = dict(sorted(totals.items()))
    return result
