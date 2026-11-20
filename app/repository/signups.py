"""Online signups and their identity checks (docs/decisions/self-care.md "Online signup")."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning

# Waits after the 1st, 2nd, ... temporary error of an identity check; beyond, the last one.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
# before payment, a signup without progress for this long expires
EXPIRE_AFTER = timedelta(hours=24)
# the states that expire (the others wait for the identity service, an operator or the worker)
EXPIRING = ("started", "identityPassed")
CLOSED = ("active", "failed", "expired", "identityFailed")


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM care_signup WHERE id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (uid,))


def insert(
    conn: psycopg.Connection,
    *,
    email: str,
    offering_id: UUID,
    client_address: str,
    code_hash: str,
    code_expires_at: datetime,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "INSERT INTO care_signup (id, email, product_offering_id, client_address,"
        " email_code_hash, email_code_expires_at, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (uuid4(), email, offering_id, client_address, code_hash, code_expires_at, now, now),
    )


def update(conn: psycopg.Connection, uid: UUID, now: datetime, **values: Any) -> Row:
    """Set the columns and `updated_at` (progress); jsonb for a dict."""
    values = {k: jsonb(v) if isinstance(v, dict) else v for k, v in values.items()}
    values["updated_at"] = now
    return returning(
        conn,
        sql.SQL("UPDATE care_signup SET {} WHERE id = %s RETURNING *").format(
            sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(column)) for column in values
            )
        ),
        [*values.values(), uid],
    )


def code_failed(conn: psycopg.Connection, uid: UUID) -> None:
    """A wrong code counts without being progress."""
    conn.execute(
        "UPDATE care_signup SET email_code_attempts = email_code_attempts + 1 WHERE id = %s",
        (uid,),
    )


def count_since(conn: psycopg.Connection, column: str, value: str, since: datetime) -> int:
    """Signups started from this email or address since `since` (the abuse limits)."""
    assert column in ("email", "client_address")
    (count,) = conn.execute(
        sql.SQL("SELECT count(*) FROM care_signup WHERE {} = %s AND created_at > %s").format(
            sql.Identifier(column)
        ),
        (value, since),
    ).fetchone()
    return count


def expired(row: Row, now: datetime) -> bool:
    """Before payment, 24 hours without progress end a signup (a top-up in flight is
    progress the gateway owes)."""
    return (
        row["state"] in EXPIRING
        and row["payment_command_id"] is None
        and row["updated_at"] <= now - EXPIRE_AFTER
    )


def due_to_expire(conn: psycopg.Connection, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT id FROM care_signup WHERE state = ANY(%s) AND payment_command_id IS NULL"
        " AND updated_at <= %s ORDER BY updated_at, id",
        (list(EXPIRING), now - EXPIRE_AFTER),
    )


def in_state(conn: psycopg.Connection, state: str) -> list[Row]:
    return fetch_all(
        conn, "SELECT id FROM care_signup WHERE state = %s ORDER BY updated_at, id", (state,)
    )


def paying(conn: psycopg.Connection) -> list[Row]:
    """identityPassed signups whose first top-up is with the gateway."""
    return fetch_all(
        conn,
        "SELECT id FROM care_signup WHERE state = 'identityPassed'"
        " AND payment_command_id IS NOT NULL ORDER BY updated_at, id",
    )


def page(
    conn: psycopg.Connection, *, state: str | None, email: str | None, offset: int, limit: int
) -> tuple[list[Row], bool]:
    where, params = [], []
    if state is not None:
        where.append("state = %s")
        params.append(state)
    if email is not None:
        where.append("email = %s")
        params.append(email)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = fetch_all(
        conn,
        f"SELECT * FROM care_signup {clause} ORDER BY created_at DESC, id"  # noqa: S608 - fixed clauses
        " OFFSET %s LIMIT %s",
        (*params, offset, limit + 1),
    )
    return rows[:limit], len(rows) > limit


# --- identity checks ---


def insert_check(
    conn: psycopg.Connection, signup_id: UUID, request: dict[str, Any], now: datetime
) -> Row:
    return returning(
        conn,
        "INSERT INTO identity_check (id, care_signup_id, request, created_at)"
        " VALUES (%s, %s, %s, %s) RETURNING *",
        (uuid4(), signup_id, jsonb(request), now),
    )


def get_check(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM identity_check WHERE id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (uid,))


def checks_due(conn: psycopg.Connection, now: datetime) -> list[Row]:
    """Checks to send (pending, past their wait) or to ask again (sent)."""
    return fetch_all(
        conn,
        "SELECT id FROM identity_check WHERE state = 'sent'"
        " OR (state = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= %s))"
        " ORDER BY created_at, id",
        (now,),
    )


def check_sent(conn: psycopg.Connection, uid: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE identity_check SET state = 'sent', attempted_at = %s,"
        " attempts = attempts + CASE WHEN state = 'pending' THEN 1 ELSE 0 END,"
        " last_error = NULL, next_attempt_at = NULL WHERE id = %s RETURNING *",
        (now, uid),
    )


def check_decided(
    conn: psycopg.Connection, uid: UUID, state: str, reason: str | None, now: datetime
) -> Row:
    return returning(
        conn,
        "UPDATE identity_check SET state = %s, reason = %s, decided_at = %s, attempted_at = %s,"
        " last_error = NULL, next_attempt_at = NULL WHERE id = %s RETURNING *",
        (state, reason, now, now, uid),
    )


def check_temporary(
    conn: psycopg.Connection, uid: UUID, *, error: str, max_attempts: int, now: datetime
) -> Row:
    """A temporary error: wait and send again, or `exhausted` once the attempts are used."""
    row = get_check(conn, uid, lock=True)
    assert row is not None
    attempts = row["attempts"] + 1
    if attempts >= max_attempts:
        return returning(
            conn,
            "UPDATE identity_check SET state = 'exhausted', attempts = %s, last_error = %s,"
            " attempted_at = %s, next_attempt_at = NULL WHERE id = %s RETURNING *",
            (attempts, error, now, uid),
        )
    wait = BACKOFF[min(attempts, len(BACKOFF)) - 1]
    return returning(
        conn,
        "UPDATE identity_check SET state = 'pending', attempts = %s, last_error = %s,"
        " attempted_at = %s, next_attempt_at = %s WHERE id = %s RETURNING *",
        (attempts, error, now, now + wait, uid),
    )


def pool_number(conn: psycopg.Connection, today, quarantine_days: int) -> Row | None:
    """The oldest unlocked pool number with no claim today or later and out of quarantine,
    locked (SKIP LOCKED: two signups never take the same one)."""
    return fetch_one(
        conn,
        """SELECT n.* FROM subscriber_number n
        WHERE n.resource_specification_id = 'msisdn' AND n.origin = 'pool'
          AND n.administrative_state = 'unlocked'
          AND NOT EXISTS (
            SELECT 1 FROM number_assignment a
            WHERE a.subscriber_number_id = n.id AND a.cancelled_at IS NULL
              AND NOT isempty(a.valid_for)
              AND (upper_inf(a.valid_for) OR upper(a.valid_for) > %(today)s
                OR a.released_on + GREATEST(%(days)s, a.aging_days) > %(today)s))
        ORDER BY n.created_at, n.value LIMIT 1 FOR UPDATE SKIP LOCKED""",
        {"today": today, "days": quarantine_days},
    )
