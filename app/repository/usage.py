"""Usage records and allowance thresholds (docs/decisions/usage.md)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.repository._sql import Row, fetch_all, fetch_one

KINDS = ("data", "voice", "sms")


def get(conn: psycopg.Connection, uid: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM usage_record WHERE id = %s", (uid,))


def by_network_id(conn: psycopg.Connection, network_record_id: str) -> Row | None:
    """The stored record of a network id, whatever its event date (the plain index on
    network_record_id). Two rows only after the accepted race of two concurrent first
    inserts with different dates: the first stored answers."""
    return fetch_one(
        conn,
        "SELECT * FROM usage_record WHERE network_record_id = %s ORDER BY created_at, id LIMIT 1",
        (network_record_id,),
    )


def product_row(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT id AS product_id, billing_account_id FROM product WHERE id = %s",
        (product_id,),
    )


def product_on(
    conn: psycopg.Connection, msisdn: str | None, imsi: str | None, day: date
) -> Row | None:
    """The product that held the MSISDN (else the IMSI) on `day`, from number assignment
    history; a cancelled or released claim does not count."""
    for value, kind in ((msisdn, "msisdn"), (imsi, "imsi")):
        if value is None:
            continue
        row = fetch_one(
            conn,
            "SELECT p.id AS product_id, p.billing_account_id FROM number_assignment a"
            " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " JOIN product p ON p.id = a.product_id"
            " WHERE n.resource_specification_id = %s AND n.value = %s"
            " AND a.cancelled_at IS NULL AND a.valid_for @> %s::date"
            " ORDER BY a.effective_from DESC LIMIT 1",
            (kind, value, day),
        )
        if row is not None:
            return row
    return None


def insert(
    conn: psycopg.Connection,
    *,
    network_record_id: str,
    kind: str,
    quantity: int,
    roaming: bool,
    throttled: bool,
    msisdn: str | None,
    imsi: str | None,
    event_at: datetime,
    event_date: date,
    product: Row | None,
    now: datetime,
) -> Row | None:
    """The new row, or None when the network's record id is already stored for the date
    (the unique key is per event_date: the table is partitioned by it)."""
    return fetch_one(
        conn,
        "INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled,"
        " msisdn, imsi, event_at, event_date, product_id, billing_account_id, status,"
        " rejection_reason, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (network_record_id, event_date) DO NOTHING RETURNING *",
        (
            uuid4(),
            network_record_id,
            kind,
            quantity,
            roaming,
            throttled,
            msisdn,
            imsi,
            event_at,
            event_date,
            None if product is None else product["product_id"],
            None if product is None else product["billing_account_id"],
            "rejected" if product is None else "notRated",
            "noLine" if product is None else None,
            now,
        ),
    )


def period_of(conn: psycopg.Connection, product_id: UUID, day: date) -> Row | None:
    """The plan period (funded interval, half-open) covering `day`: the latest grant when a
    mid-period change left two."""
    return fetch_one(
        conn,
        "SELECT lower(period) AS period_start, upper(period) AS period_end"
        " FROM funded_interval WHERE product_id = %s AND period @> %s::date"
        " ORDER BY lower(period) DESC, authorized_at DESC LIMIT 1",
        (product_id, day),
    )


def used(conn: psycopg.Connection, product_id: UUID, start: date, end: date) -> dict[str, int]:
    """Period totals per kind (bytes, seconds, messages)."""
    rows = fetch_all(
        conn,
        "SELECT kind, sum(quantity)::bigint AS total FROM usage_record"
        " WHERE product_id = %s AND event_date >= %s AND event_date < %s GROUP BY kind",
        (product_id, start, end),
    )
    return {kind: 0 for kind in KINDS} | {row["kind"]: row["total"] for row in rows}


def latest_throttled(
    conn: psycopg.Connection, product_id: UUID, start: date, end: date
) -> bool | None:
    """What the network said on the period's latest record; None without records."""
    row = fetch_one(
        conn,
        "SELECT throttled FROM usage_record WHERE product_id = %s"
        " AND event_date >= %s AND event_date < %s ORDER BY event_at DESC, id LIMIT 1",
        (product_id, start, end),
    )
    return None if row is None else row["throttled"]


def told_profile(conn: psycopg.Connection, product_id: UUID) -> dict[str, Any] | None:
    """The profile the network was last told for the line: the latest confirmed activate or
    change_profile (an add-on's raised allowance is such a change_profile)."""
    row = fetch_one(
        conn,
        "SELECT profile FROM network_command WHERE product_id = %s AND state = 'confirmed'"
        " AND kind IN ('activate', 'change_profile')"
        " ORDER BY confirmed_at DESC, created_at DESC, (kind = 'change_profile') DESC LIMIT 1",
        (product_id,),
    )
    return None if row is None else row["profile"]


def insert_threshold(
    conn: psycopg.Connection,
    *,
    product_id: UUID,
    period_start: date,
    period_end: date,
    allowance: str,
    threshold: int,
    record_id: UUID,
    used_amount: int,
    included: int,
    now: datetime,
) -> bool:
    """True when this is the first time the threshold is reached in the period."""
    row = fetch_one(
        conn,
        "INSERT INTO usage_threshold (product_id, period_start, period_end, allowance,"
        " threshold, usage_record_id, used, included, reached_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING threshold",
        (
            product_id,
            period_start,
            period_end,
            allowance,
            threshold,
            record_id,
            used_amount,
            included,
            now,
        ),
    )
    return row is not None


def thresholds(conn: psycopg.Connection, product_id: UUID, start: date) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM usage_threshold WHERE product_id = %s AND period_start = %s"
        " ORDER BY allowance, threshold",
        (product_id, start),
    )


_COLUMNS = {
    "id": "id",
    "usage_type": "kind",
    "status": "status",
    "product_id": "product_id",
    "billing_account_id": "billing_account_id",
}


def page(
    conn: psycopg.Connection, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    conditions = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(_COLUMNS[key])) for key in where
    )
    clause = sql.SQL(" WHERE ") + conditions if where else sql.SQL("")
    params = list(where.values())
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM usage_record")
        + clause
        + sql.SQL(" ORDER BY event_at DESC, id OFFSET %s LIMIT %s"),
        [*params, offset, limit + 1],
    )
    return rows[:limit], len(rows) > limit


# --- rating of pay-as-you-go usage (docs/decisions/usage.md, "Rating") ---


def rating_accounts(conn: psycopg.Connection, today: date) -> list[UUID]:
    """Accounts with usage of a pay-as-you-go line on a closed day that is not rated yet."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT u.billing_account_id FROM usage_record u"
        " JOIN product p ON p.id = u.product_id"
        " JOIN billing_account a ON a.id = u.billing_account_id"
        " WHERE u.status = 'notRated' AND u.event_date < %s AND p.grantless"
        " AND p.parent_product_id IS NULL AND a.rating_type = 'prepaid'"
        " ORDER BY u.billing_account_id",
        (today,),
    )
    return [row["billing_account_id"] for row in rows]


def unrated(conn: psycopg.Connection, account_id: UUID, today: date, limit: int) -> list[Row]:
    """The account's unrated records of pay-as-you-go lines on closed days, oldest first."""
    return fetch_all(
        conn,
        "SELECT u.* FROM usage_record u JOIN product p ON p.id = u.product_id"
        " WHERE u.billing_account_id = %s AND u.status = 'notRated' AND u.event_date < %s"
        " AND p.grantless AND p.parent_product_id IS NULL"
        " AND NOT EXISTS (SELECT FROM usage_charge c WHERE c.usage_record_id = u.id)"
        " ORDER BY u.event_date, u.event_at, u.id LIMIT %s FOR UPDATE OF u",
        (account_id, today, limit),
    )


def insert_charge(
    conn: psycopg.Connection,
    *,
    record_id: UUID,
    event_date: date,
    command_id: UUID,
    account_id: UUID,
    product_id: UUID,
    price_id: UUID | None,
    abr_id: UUID | None,
    blocks: int | None,
    now: datetime,
) -> None:
    """Mark the record rated, once: the primary key refuses a second charge. The event date
    names the record's partition."""
    conn.execute(
        "INSERT INTO usage_charge (usage_record_id, command_id, billing_account_id, product_id,"
        " price_id, abr_id, blocks, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (record_id, command_id, account_id, product_id, price_id, abr_id, blocks, now),
    )
    conn.execute(
        "UPDATE usage_record SET status = 'rated' WHERE id = %s AND event_date = %s",
        (record_id, event_date),
    )


def charges_of(conn: psycopg.Connection, record_id: UUID) -> Row | None:
    """How a rated record was charged: the price applied and its ABR."""
    return charges_by_record(conn, [record_id]).get(record_id)


def charges_by_record(conn: psycopg.Connection, record_ids: list[UUID]) -> dict[UUID, Row]:
    """Applied prices of all rated records in a response page, in one query."""
    if not record_ids:
        return {}
    rows = fetch_all(
        conn,
        "SELECT c.*, p.name AS price_name, p.amount AS price_amount, p.currency AS price_currency,"
        " p.usage_block, p.details AS price_details FROM usage_charge c"
        " LEFT JOIN product_offering_price p ON p.id = c.price_id"
        " WHERE c.usage_record_id = ANY(%s)",
        (record_ids,),
    )
    return {row["usage_record_id"]: row for row in rows}


def unrated_count(conn: psycopg.Connection, product_id: UUID) -> int:
    row = fetch_one(
        conn,
        "SELECT count(*) AS n FROM usage_record WHERE product_id = %s AND status = 'notRated'",
        (product_id,),
    )
    assert row is not None
    return row["n"]
