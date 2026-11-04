"""TMF654 bucket and accumulatedBalance reads: views of the funding ledger, no stored balance.

One bucket per prepaid account and currency (prepaid_bucket). The figures come from one
statement, so ledger L and holds H are read from the same snapshot without locking the
account; reads never write and never create a bucket.
"""

from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository import billing
from app.repository._sql import Row, fetch_all, fetch_one

# Bucket status from the owning account's state (TMF654 BucketStatusType).
STATUS = sql.SQL(
    "CASE a.state WHEN 'Active' THEN 'active' WHEN 'Suspended' THEN 'suspended' ELSE 'expired' END"
)
_BUCKETS = sql.SQL(
    """WITH bucket AS (
        SELECT b.id, b.accumulated_balance_id, b.billing_account_id, b.currency,
               b.created_at, a.name AS account_name, a.description AS account_description,
               a.state::text AS account_state, a.customer_id, c.name AS customer_name,
               {status} AS status, 'monetary' AS usage_type
        FROM prepaid_bucket b
        JOIN billing_account a ON a.id = b.billing_account_id
        JOIN customer c ON c.id = a.customer_id
    ) """
).format(status=STATUS)
_FIGURES = sql.SQL(
    """SELECT ({}) + ({}) AS ledger,
        (SELECT coalesce(sum(s.amount), 0) FROM funding_hold_source s
         JOIN funding_hold h ON h.id = s.hold_id
         WHERE h.billing_account_id = %(id)s AND h.released_at IS NULL) AS reserved"""
).format(sql.SQL(billing.PAYMENTS_LESS_CHARGES), sql.SQL(billing.BALANCE_ACTION_LEDGER))


def page(
    conn: psycopg.Connection, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    conditions = sql.SQL(" AND ").join(
        sql.SQL("{}::text = %s").format(sql.Identifier(key)) for key in where
    )
    clause = sql.SQL(" WHERE ") + conditions if where else sql.SQL("")
    params = [str(value) for value in where.values()]
    total = fetch_one(conn, _BUCKETS + sql.SQL("SELECT count(*) AS n FROM bucket") + clause, params)
    assert total is not None
    rows = fetch_all(
        conn,
        _BUCKETS
        + sql.SQL("SELECT * FROM bucket")
        + clause
        + sql.SQL(" ORDER BY created_at, id OFFSET %s LIMIT %s"),
        [*params, offset, limit],
    )
    return rows, total["n"]


def get(conn: psycopg.Connection, column: str, uid: UUID) -> Row | None:
    """The bucket whose `id` or `accumulated_balance_id` is `uid`."""
    return fetch_one(
        conn,
        _BUCKETS + sql.SQL("SELECT * FROM bucket WHERE {} = %s").format(sql.Identifier(column)),
        (uid,),
    )


def figures(conn: psycopg.Connection, account_id: UUID) -> Row:
    """Ledger L (TMF666 currentBalance) and outstanding holds H (reservedValue)."""
    row = fetch_one(conn, _FIGURES, {"id": account_id})
    assert row is not None
    return row


def live_products(conn: psycopg.Connection, account_id: UUID, today: date) -> list[Row]:
    """Products not yet ended on `today` (the prepaid close guard's predicate)."""
    return fetch_all(
        conn,
        "SELECT id, name FROM product WHERE billing_account_id = %(id)s"
        " AND status NOT IN ('terminated', 'cancelled', 'aborted')"
        " AND (termination_date IS NULL OR termination_date > %(today)s)"
        " ORDER BY created_at, id",
        {"id": account_id, "today": today},
    )


def numbers(conn: psycopg.Connection, product_ids: list[UUID], today: date) -> list[Row]:
    """The number each live product holds: today's assignment, else its next accepted
    claim (a pending start). One per product; a scheduled number change does not count."""
    return fetch_all(
        conn,
        """SELECT DISTINCT ON (a.product_id) n.id, n.name, a.product_id
        FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id
        WHERE a.product_id = ANY(%(ids)s) AND a.cancelled_at IS NULL
          AND a.resource_specification_id = 'msisdn' AND NOT isempty(a.valid_for)
          AND (a.released_on IS NULL OR a.released_on > %(today)s)
        ORDER BY a.product_id, a.effective_from""",
        {"ids": product_ids, "today": today},
    )
