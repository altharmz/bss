"""Reads and writes of the account data pool (docs/decisions/usage.md, "Pooled data").

Every read takes many keys in one statement, so the feed's batch (`ingest_batch`) keeps its
one-statement-per-step shape.
"""

from datetime import date, datetime
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one

# (billing account, pool period start, pool period end)
Key = tuple[UUID, date, date]


def cycle_days(conn: psycopg.Connection, account_ids: list[UUID]) -> dict[UUID, int]:
    rows = fetch_all(
        conn,
        "SELECT id, bill_cycle_day FROM billing_account WHERE id = ANY(%s::uuid[])",
        (account_ids,),
    )
    return {row["id"]: row["bill_cycle_day"] for row in rows}


def lines(conn: psycopg.Connection, keys: list[Key]) -> list[Row]:
    """The plan lines and their add-ons that are live on some day of each pool period, with
    the offering each had on its first day in the period (its share is fixed by then). Pay
    as you go lines (grantless, no plan) are never in a pool."""
    return fetch_all(
        conn,
        "SELECT i.account_id, i.period_start, p.id AS product_id, p.parent_product_id,"
        " p.grantless, p.start_date, p.termination_date,"
        " greatest(p.start_date, i.period_start) AS first_day,"
        " product_offering_on(p.id, greatest(p.start_date, i.period_start)) AS offering_id"
        " FROM unnest(%s::uuid[], %s::date[], %s::date[])"
        " AS i(account_id, period_start, period_end)"
        " JOIN product p ON p.billing_account_id = i.account_id"
        " WHERE p.start_date < i.period_end"
        " AND (p.termination_date IS NULL OR p.termination_date > i.period_start)"
        " AND p.status NOT IN ('cancelled', 'aborted')"
        " AND (NOT p.grantless OR p.parent_product_id IS NOT NULL)"
        " ORDER BY i.account_id, i.period_start, p.start_date, p.id",
        ([k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys]),
    )


def used(conn: psycopg.Connection, members: list[tuple[Key, UUID]]) -> dict[Key, int]:
    """Domestic data (bytes) of the members of each pool in its period. Data has no
    international zone, so domestic data is data not roaming."""
    rows = fetch_all(
        conn,
        "SELECT i.account_id, i.period_start, i.period_end, coalesce(sum(u.total), 0)::bigint"
        " AS total FROM unnest(%s::uuid[], %s::date[], %s::date[], %s::uuid[])"
        " AS i(account_id, period_start, period_end, product_id)"
        " CROSS JOIN LATERAL (SELECT sum(quantity) AS total FROM usage_record"
        " WHERE product_id = i.product_id AND kind = 'data' AND NOT roaming"
        " AND event_date >= i.period_start AND event_date < i.period_end) u"
        " GROUP BY 1, 2, 3",
        (
            [k[0] for k, _ in members],
            [k[1] for k, _ in members],
            [k[2] for k, _ in members],
            [m for _, m in members],
        ),
    )
    totals = {key: 0 for key, _ in members}
    for row in rows:
        totals[(row["account_id"], row["period_start"], row["period_end"])] = row["total"]
    return totals


def insert_threshold(
    conn: psycopg.Connection,
    *,
    key: Key,
    threshold: int,
    product_id: UUID,
    record_id: UUID,
    used_amount: int,
    included: int,
    now: datetime,
) -> bool:
    """True when this is the first time the pool reaches the threshold in the period."""
    row = fetch_one(
        conn,
        "INSERT INTO usage_pool_threshold (billing_account_id, period_start, period_end,"
        " threshold, product_id, usage_record_id, used, included, reached_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING threshold",
        (key[0], key[1], key[2], threshold, product_id, record_id, used_amount, included, now),
    )
    return row is not None


def thresholds(conn: psycopg.Connection, account_id: UUID, start: date) -> list[int]:
    rows = fetch_all(
        conn,
        "SELECT threshold FROM usage_pool_threshold WHERE billing_account_id = %s"
        " AND period_start = %s ORDER BY threshold",
        (account_id, start),
    )
    return [row["threshold"] for row in rows]


def accounts_with_lines(
    conn: psycopg.Connection, offering_ids: list[UUID], today: date
) -> list[UUID]:
    """Accounts with a plan line live today that has, or had, one of the pooled offerings:
    the candidates the worker looks at for pool changes. The pool itself decides."""
    if not offering_ids:
        return []
    rows = fetch_all(
        conn,
        "SELECT DISTINCT p.billing_account_id FROM product p WHERE NOT p.grantless"
        " AND p.start_date <= %(today)s"
        " AND (p.termination_date IS NULL OR p.termination_date > %(today)s)"
        " AND p.status NOT IN ('cancelled', 'aborted')"
        " AND (p.product_offering_id = ANY(%(o)s::uuid[]) OR EXISTS ("
        "SELECT FROM product_offering_change c WHERE c.product_id = p.id"
        " AND c.product_offering_id = ANY(%(o)s::uuid[])))"
        " ORDER BY 1",
        {"today": today, "o": offering_ids},
    )
    return [row["billing_account_id"] for row in rows]


def network_offerings(conn: psycopg.Connection) -> list[Row]:
    """Every offering of a specification, with both documents, for the pooled-offering set."""
    return fetch_all(
        conn,
        "SELECT o.id, o.details AS offering_details, s.details AS spec_details"
        " FROM product_offering o JOIN product_specification s"
        " ON s.id = o.product_specification_id ORDER BY o.id",
    )
