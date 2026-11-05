"""Charges sent to the payment gateway (docs/decisions/payments.md).

One command per card top-up or automatic top-up. Its id is the idempotency key sent to the
gateway, so every resend names the same charge and the gateway never charges twice.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning

# Waits after the 1st, 2nd, ... temporary error; beyond the table, the last one.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM payment_command WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid,))


def by_client_key(conn: psycopg.Connection, account_id: UUID, key: str) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM payment_command WHERE billing_account_id = %s AND client_key = %s",
        (account_id, key),
    )


def insert(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    method_id: UUID,
    purpose: str,
    amount: Decimal,
    currency: str,
    client_key: str | None,
    request: dict[str, Any],
    now: datetime,
    renewal_command_id: UUID | None = None,
) -> Row:
    return returning(
        conn,
        "INSERT INTO payment_command (billing_account_id, payment_method_id, purpose, amount,"
        " currency, client_key, request, created_at, renewal_command_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            account_id,
            method_id,
            purpose,
            amount,
            currency,
            client_key,
            jsonb(request),
            now,
            renewal_command_id,
        ),
    )


def to_send(conn: psycopg.Connection, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM payment_command WHERE state = 'pending'"
        " AND (next_attempt_at IS NULL OR next_attempt_at <= %s) ORDER BY created_at, id",
        (now,),
    )


def awaiting(conn: psycopg.Connection) -> list[Row]:
    return fetch_all(
        conn, "SELECT * FROM payment_command WHERE state = 'sent' ORDER BY created_at, id"
    )


def mark_sent(conn: psycopg.Connection, uid: UUID, *, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE payment_command SET state = 'sent', sent_at = %s, attempts = attempts + 1,"
        " last_error = NULL, next_attempt_at = NULL WHERE id = %s RETURNING *",
        (now, uid),
    )


def decline(conn: psycopg.Connection, uid: UUID, *, reason: str) -> Row:
    return returning(
        conn,
        "UPDATE payment_command SET state = 'declined', decline_reason = %s,"
        " next_attempt_at = NULL WHERE id = %s RETURNING *",
        (reason, uid),
    )


def confirm(
    conn: psycopg.Connection,
    uid: UUID,
    *,
    gateway_ref: str,
    payment_id: UUID,
    topup_id: UUID,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "UPDATE payment_command SET state = 'confirmed', confirmed_at = %s, gateway_ref = %s,"
        " payment_id = %s, topup_id = %s WHERE id = %s RETURNING *",
        (now, gateway_ref, payment_id, topup_id, uid),
    )


def temporary(
    conn: psycopg.Connection, uid: UUID, *, error: str, max_attempts: int, now: datetime
) -> Row:
    """A temporary error: wait and submit again, or `exhausted` once the attempts are used."""
    row = get(conn, uid, lock=True)
    attempts = row["attempts"] + 1
    if attempts >= max_attempts:
        return returning(
            conn,
            "UPDATE payment_command SET state = 'exhausted', attempts = %s, last_error = %s,"
            " next_attempt_at = NULL WHERE id = %s RETURNING *",
            (attempts, error, uid),
        )
    wait = BACKOFF[min(attempts, len(BACKOFF)) - 1]
    return returning(
        conn,
        "UPDATE payment_command SET attempts = %s, last_error = %s, next_attempt_at = %s"
        " WHERE id = %s RETURNING *",
        (attempts, error, now + wait, uid),
    )


def retry(conn: psycopg.Connection, uid: UUID) -> Row:
    """An exhausted command goes back to pending with its identity and request."""
    return returning(
        conn,
        "UPDATE payment_command SET state = 'pending', attempts = 0, next_attempt_at = NULL"
        " WHERE id = %s AND state = 'exhausted' RETURNING *",
        (uid,),
    )


def page(
    conn: psycopg.Connection,
    *,
    account_id: UUID | None,
    state: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], int]:
    where = (  # noqa: S608 - fixed text, the values are parameters
        " WHERE (%(a)s::uuid IS NULL OR billing_account_id = %(a)s)"
        " AND (%(s)s::text IS NULL OR state = %(s)s)"
    )
    args = {"a": account_id, "s": state, "off": offset, "lim": limit}
    total = fetch_one(conn, f"SELECT count(*) AS n FROM payment_command{where}", args)["n"]  # noqa: S608
    rows = fetch_all(
        conn,
        f"SELECT * FROM payment_command{where} ORDER BY created_at DESC, id"  # noqa: S608
        " LIMIT %(lim)s OFFSET %(off)s",
        args,
    )
    return rows, total


def auto_candidates(conn: psycopg.Connection) -> list[Row]:
    """Prepaid accounts with auto top-up on, no automatic charge in flight and no declined
    one since the settings or a payment last changed."""
    return fetch_all(
        conn,
        "SELECT a.id, a.currency, a.auto_topup_threshold, a.auto_topup_amount"
        " FROM billing_account a"
        " WHERE a.auto_topup_enabled AND a.rating_type = 'prepaid'"
        " AND NOT EXISTS (SELECT FROM payment_command c WHERE c.billing_account_id = a.id"
        " AND c.purpose = 'auto_topup' AND c.state IN ('pending', 'sent'))"
        " AND NOT EXISTS (SELECT FROM payment_command c WHERE c.billing_account_id = a.id"
        " AND c.purpose = 'auto_topup' AND c.state IN ('declined', 'exhausted')"
        " AND c.created_at >= COALESCE(a.auto_topup_changed_at, '-infinity')"
        " AND NOT EXISTS (SELECT FROM payment p WHERE p.billing_account_id = a.id"
        " AND p.status = 'done' AND p.received_at > c.created_at))"
        " ORDER BY a.id",
    )


def renewal_candidates(conn: psycopg.Connection) -> list[Row]:
    """The oldest pending renewal of each prepaid account with auto-pay on (the auto top-up
    setting), whose latest recorded shortfall is open, with no renewal charge in flight and
    none declined or exhausted since that shortfall was observed. Its `shortfall` is what
    the card is charged for (its `required` less the spendable balance, read under the lock)."""
    return fetch_all(
        conn,
        "SELECT DISTINCT ON (pc.billing_account_id) pc.id AS command_id,"
        " a.id AS account_id, a.currency, s.required"
        " FROM prepaid_command pc JOIN billing_account a ON a.id = pc.billing_account_id"
        " JOIN LATERAL (SELECT * FROM prepaid_funding_shortfall WHERE command_id = pc.id"
        "   ORDER BY observed_at DESC LIMIT 1) s ON true"
        " WHERE pc.kind = 'renewal' AND pc.state <> 'completed' AND a.auto_topup_enabled"
        " AND a.rating_type = 'prepaid' AND a.state = 'Active' AND s.required > s.available"
        " AND NOT EXISTS (SELECT FROM payment_command c WHERE c.billing_account_id = a.id"
        "   AND c.purpose = 'renewal' AND c.state IN ('pending', 'sent'))"
        " AND NOT EXISTS (SELECT FROM payment_command c WHERE c.renewal_command_id = pc.id"
        "   AND c.state IN ('declined', 'exhausted') AND c.created_at >= s.observed_at)"
        " ORDER BY pc.billing_account_id, pc.due_date, pc.id",
    )
