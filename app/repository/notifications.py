"""Customer messages (TMF681) and marketing consent (docs/decisions/notifications.md)."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.repository._sql import Row, fetch_all, fetch_one, jsonb

CHANNELS = ("sms", "email")
STATES = ("initial", "inProgress", "completed", "cancelled", "failed")
CATEGORIES = ("transactional", "marketing")
OPEN = ("initial", "inProgress")
BACKOFF_MINUTES = (1, 5, 15, 60)

_COLUMNS = {
    "id": "id",
    "state": "state",
    "channel": "channel",
    "customer_id": "customer_id",
    "billing_account_id": "billing_account_id",
    "product_id": "product_id",
}


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM notification_message WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid,))


def page(
    conn: psycopg.Connection, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    conditions = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(_COLUMNS[key])) for key in where
    )
    clause = sql.SQL(" WHERE ") + conditions if where else sql.SQL("")
    params = list(where.values())
    total = fetch_one(
        conn, sql.SQL("SELECT count(*) AS n FROM notification_message") + clause, params
    )
    assert total is not None
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM notification_message")
        + clause
        + sql.SQL(" ORDER BY created_at DESC, id OFFSET %s LIMIT %s"),
        [*params, offset, limit],
    )
    return rows, total["n"]


def insert(
    conn: psycopg.Connection,
    *,
    event_type: str,
    category: str,
    channel: str,
    template: str,
    state: str,
    state_reason: str | None,
    receiver_name: str | None,
    receiver_email: str | None,
    receiver_phone: str | None,
    customer_id: UUID | None,
    billing_account_id: UUID | None,
    product_id: UUID | None,
    subject: str | None,
    content: str,
    now: datetime,
    dedupe_key: str | None = None,
    scheduled_send_time: datetime | None = None,
    description: str | None = None,
    priority: str | None = None,
    sender: dict[str, Any] | None = None,
    resend_of: UUID | None = None,
) -> Row | None:
    """The new message, or None when the event's message (dedupe key and channel) exists."""
    open_ = state in OPEN
    return fetch_one(
        conn,
        "INSERT INTO notification_message (id, event_type, category, channel, template, state,"
        " state_reason, receiver_name, receiver_email, receiver_phone, customer_id,"
        " billing_account_id, product_id, subject, content, description, priority, sender,"
        " dedupe_key, scheduled_send_time, next_attempt_at, resend_of, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s) ON CONFLICT (dedupe_key, channel) DO NOTHING RETURNING *",
        (
            uuid4(),
            event_type,
            category,
            channel,
            template,
            state,
            state_reason,
            receiver_name,
            receiver_email,
            receiver_phone,
            customer_id,
            billing_account_id,
            product_id,
            subject,
            content,
            description,
            priority,
            None if sender is None else jsonb(sender),
            dedupe_key,
            scheduled_send_time,
            (scheduled_send_time or now) if open_ else None,
            resend_of,
            now,
        ),
    )


def event_exists(conn: psycopg.Connection, dedupe_key: str) -> bool:
    return (
        fetch_one(
            conn, "SELECT 1 FROM notification_message WHERE dedupe_key = %s LIMIT 1", (dedupe_key,)
        )
        is not None
    )


def due(conn: psycopg.Connection, now: datetime, limit: int = 200) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM notification_message WHERE state IN ('initial', 'inProgress')"
        " AND next_attempt_at <= %s ORDER BY next_attempt_at, id LIMIT %s",
        (now, limit),
    )


def completed(conn: psycopg.Connection, uid: UUID, *, ref: str, now: datetime) -> Row:
    return _one(
        conn,
        "UPDATE notification_message SET state = 'completed', try_times = try_times + 1,"
        " send_time = COALESCE(send_time, %s), send_time_complete = %s, provider_ref = %s,"
        " next_attempt_at = NULL, last_error = NULL WHERE id = %s RETURNING *",
        (now, now, ref, uid),
    )


def failed_attempt(
    conn: psycopg.Connection, uid: UUID, *, error: str, max_attempts: int, now: datetime
) -> Row:
    """One failed send: a retry with backoff, or `failed` once the attempts are used up."""
    row = get(conn, uid, lock=True)
    assert row is not None
    tries = row["try_times"] + 1
    if tries >= max_attempts:
        return _one(
            conn,
            "UPDATE notification_message SET state = 'failed', try_times = %s,"
            " send_time = COALESCE(send_time, %s), next_attempt_at = NULL, last_error = %s"
            " WHERE id = %s RETURNING *",
            (tries, now, error, uid),
        )
    wait = BACKOFF_MINUTES[min(tries, len(BACKOFF_MINUTES)) - 1]
    return _one(
        conn,
        "UPDATE notification_message SET state = 'inProgress', try_times = %s,"
        " send_time = COALESCE(send_time, %s), next_attempt_at = %s, last_error = %s"
        " WHERE id = %s RETURNING *",
        (tries, now, now + timedelta(minutes=wait), error, uid),
    )


def defer(conn: psycopg.Connection, uid: UUID, *, until: datetime) -> Row:
    """Wait for the end of the quiet hours."""
    return _one(
        conn,
        "UPDATE notification_message SET scheduled_send_time = %s, next_attempt_at = %s"
        " WHERE id = %s RETURNING *",
        (until, until, uid),
    )


def cancel(conn: psycopg.Connection, uid: UUID, *, reason: str) -> Row:
    return _one(
        conn,
        "UPDATE notification_message SET state = 'cancelled', state_reason = %s,"
        " next_attempt_at = NULL WHERE id = %s RETURNING *",
        (reason, uid),
    )


def _one(conn: psycopg.Connection, query: str, params: tuple) -> Row:
    row = fetch_one(conn, query, params)
    assert row is not None
    return row


# --- consent ---


def consent_history(conn: psycopg.Connection, customer_id: UUID, limit: int = 100) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM notification_consent WHERE customer_id = %s"
        " ORDER BY recorded_at DESC, seq DESC LIMIT %s",
        (customer_id, limit),
    )


def consent_current(conn: psycopg.Connection, customer_id: UUID) -> dict[str, Row]:
    """The latest consent row per channel (marketing)."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT ON (channel) * FROM notification_consent"
        " WHERE customer_id = %s AND category = 'marketing'"
        " ORDER BY channel, recorded_at DESC, seq DESC",
        (customer_id,),
    )
    return {row["channel"]: row for row in rows}


def opted_in(conn: psycopg.Connection, customer_id: UUID, channel: str) -> bool:
    row = consent_current(conn, customer_id).get(channel)
    return row is not None and row["opted_in"]


def record_consent(
    conn: psycopg.Connection,
    *,
    customer_id: UUID,
    channel: str,
    opted_in: bool,
    source: str,
    operator: str,
    now: datetime,
) -> Row:
    return _one(
        conn,
        "INSERT INTO notification_consent (id, customer_id, channel, category, opted_in,"
        " source, recorded_by, recorded_at) VALUES (%s, %s, %s, 'marketing', %s, %s, %s, %s)"
        " RETURNING *",
        (uuid4(), customer_id, channel, opted_in, source, operator, now),
    )
