"""Self-care logins, their sessions and one-time codes (docs/decisions/self-care.md).

Every function that reads customer data for a signed-in customer takes the `customer_id` the
session resolved to (app/care/deps.py `CareActor`), never an id from the request alone.
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning

# --- logins ---


def login_by_email(conn: psycopg.Connection, email: str, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM customer_login WHERE email = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (email,))


def login_of_customer(
    conn: psycopg.Connection, customer_id: UUID, *, lock: bool = False
) -> Row | None:
    query = "SELECT * FROM customer_login WHERE customer_id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (customer_id,))


def login_by_id(conn: psycopg.Connection, login_id: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM customer_login WHERE id = %s"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (login_id,))


def invite(
    conn: psycopg.Connection, customer_id: UUID, email: str, operator: str, now: datetime
) -> Row:
    """A new login, or the customer's disabled or never-activated one with a new email: an
    invitation starts again from a link code. Raises UniqueViolation for an email in use."""
    return returning(
        conn,
        "INSERT INTO customer_login (id, customer_id, email, created_by, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (customer_id) DO UPDATE SET"
        " email = EXCLUDED.email, password_hash = NULL, email_verified_at = NULL,"
        " disabled_at = NULL, failed_attempts = 0, locked_until = NULL,"
        " created_by = EXCLUDED.created_by, updated_at = EXCLUDED.updated_at RETURNING *",
        (uuid4(), customer_id, email, operator, now, now),
    )


def create_verified(
    conn: psycopg.Connection,
    customer_id: UUID,
    email: str,
    password_hash: str,
    created_by: str,
    now: datetime,
) -> Row:
    """An active login whose email a signup verified with a code (#377). Raises UniqueViolation
    for an email in use."""
    return returning(
        conn,
        "INSERT INTO customer_login (id, customer_id, email, password_hash, email_verified_at,"
        " created_by, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (uuid4(), customer_id, email, password_hash, now, created_by, now, now),
    )


def disable(conn: psycopg.Connection, login_id: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE customer_login SET disabled_at = COALESCE(disabled_at, %s), updated_at = %s"
        " WHERE id = %s RETURNING *",
        (now, now, login_id),
    )


def set_password(conn: psycopg.Connection, login_id: UUID, password_hash: str, now) -> Row:
    """A new password; the email is verified by the code that allowed it, the lockout ends."""
    return returning(
        conn,
        "UPDATE customer_login SET password_hash = %s,"
        " email_verified_at = COALESCE(email_verified_at, %s), failed_attempts = 0,"
        " locked_until = NULL, updated_at = %s WHERE id = %s RETURNING *",
        (password_hash, now, now, login_id),
    )


def password_ok(conn: psycopg.Connection, login_id: UUID) -> None:
    conn.execute(
        "UPDATE customer_login SET failed_attempts = 0, locked_until = NULL"
        " WHERE id = %s AND (failed_attempts <> 0 OR locked_until IS NOT NULL)",
        (login_id,),
    )


def password_failed(
    conn: psycopg.Connection, login_id: UUID, *, maximum: int, until: datetime
) -> Row:
    """Count a wrong password; at `maximum` the login locks until `until` and the count
    restarts (the account PIN's mechanics, app/repository/security.py `pin_failed`)."""
    return returning(
        conn,
        "UPDATE customer_login SET failed_attempts = CASE WHEN failed_attempts + 1 >= %s THEN 0"
        " ELSE failed_attempts + 1 END, locked_until = CASE WHEN failed_attempts + 1 >= %s"
        " THEN %s ELSE locked_until END WHERE id = %s RETURNING *",
        (maximum, maximum, until, login_id),
    )


# --- codes ---


def new_code(
    conn: psycopg.Connection,
    login_id: UUID,
    purpose: str,
    code_hash: str,
    now: datetime,
    valid: timedelta,
) -> Row:
    """A new code; the unused older codes of the purpose end with it."""
    conn.execute(
        "UPDATE customer_login_code SET used_at = %s"
        " WHERE customer_login_id = %s AND purpose = %s AND used_at IS NULL",
        (now, login_id, purpose),
    )
    return returning(
        conn,
        "INSERT INTO customer_login_code (id, customer_login_id, purpose, code_hash, created_at,"
        " expires_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (uuid4(), login_id, purpose, code_hash, now, now + valid),
    )


def open_code(conn: psycopg.Connection, login_id: UUID, purpose: str, now) -> Row | None:
    """The login's live code of the purpose (unused, unexpired), locked."""
    return fetch_one(
        conn,
        "SELECT * FROM customer_login_code WHERE customer_login_id = %s AND purpose = %s"
        " AND used_at IS NULL AND expires_at > %s ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
        (login_id, purpose, now),
    )


def link_code(conn: psycopg.Connection, code_hash: str) -> Row | None:
    """A link code (verify_email, reset_password) by its hash, used or not, locked."""
    return fetch_one(
        conn,
        "SELECT * FROM customer_login_code WHERE code_hash = %s AND purpose <> 'sign_in'"
        " FOR UPDATE",
        (code_hash,),
    )


def code_used(conn: psycopg.Connection, code_id: UUID, now: datetime) -> None:
    conn.execute("UPDATE customer_login_code SET used_at = %s WHERE id = %s", (now, code_id))


def code_failed(conn: psycopg.Connection, code_id: UUID, *, maximum: int, now) -> Row:
    """Count a wrong code; at `maximum` the code is spent."""
    return returning(
        conn,
        "UPDATE customer_login_code SET attempts = attempts + 1, used_at = CASE"
        " WHEN attempts + 1 >= %s THEN %s ELSE used_at END WHERE id = %s RETURNING *",
        (maximum, now, code_id),
    )


def end_codes(conn: psycopg.Connection, login_id: UUID, now: datetime) -> None:
    conn.execute(
        "UPDATE customer_login_code SET used_at = %s"
        " WHERE customer_login_id = %s AND used_at IS NULL",
        (now, login_id),
    )


# --- sessions ---


def new_session(
    conn: psycopg.Connection, token_hash: str, login_id: UUID, now: datetime, expires: datetime
) -> None:
    conn.execute("DELETE FROM customer_session WHERE expires_at <= %s", (now,))
    conn.execute(
        "INSERT INTO customer_session (token_hash, customer_login_id, created_at, last_seen_at,"
        " expires_at) VALUES (%s, %s, %s, %s, %s)",
        (token_hash, login_id, now, now, expires),
    )


def live_session(
    conn: psycopg.Connection, token_hash: str, now: datetime, seen_after: datetime
) -> Row | None:
    """The session and its login, when the session is live and the login enabled. A plain
    SELECT, no row lock (docs/decisions/users.md, #287)."""
    return fetch_one(
        conn,
        "SELECT s.token_hash, s.last_seen_at, s.expires_at, l.id AS login_id, l.customer_id,"
        " l.email"
        " FROM customer_session s JOIN customer_login l ON l.id = s.customer_login_id"
        " WHERE s.token_hash = %s AND s.revoked_at IS NULL AND l.disabled_at IS NULL"
        " AND s.expires_at > %s AND s.last_seen_at > %s",
        (token_hash, now, seen_after),
    )


def touch_session(conn: psycopg.Connection, token_hash: str, now: datetime, stale) -> None:
    """At most one write a minute per session: a read stays a read."""
    conn.execute(
        "UPDATE customer_session SET last_seen_at = %s WHERE token_hash = %s AND last_seen_at < %s",
        (now, token_hash, stale),
    )


def revoke_session(conn: psycopg.Connection, token_hash: str, now: datetime) -> None:
    conn.execute(
        "UPDATE customer_session SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL",
        (now, token_hash),
    )


def revoke_sessions(conn: psycopg.Connection, login_id: UUID, now: datetime) -> int:
    return conn.execute(
        "UPDATE customer_session SET revoked_at = %s"
        " WHERE customer_login_id = %s AND revoked_at IS NULL",
        (now, login_id),
    ).rowcount


def live_sessions(conn: psycopg.Connection, login_id: UUID, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT created_at, last_seen_at, expires_at FROM customer_session"
        " WHERE customer_login_id = %s AND revoked_at IS NULL AND expires_at > %s",
        (login_id, now),
    )


# --- the signed-in customer's own data ---


def own_customer(conn: psycopg.Connection, customer_id: UUID, wanted: UUID) -> Row | None:
    """The customer `wanted`, only when it is the signed-in customer: anything else is None
    (a 404, so that ids are not confirmed)."""
    if wanted != customer_id:
        return None
    return fetch_one(conn, "SELECT id, name, status FROM customer WHERE id = %s", (customer_id,))


def customer_name(conn: psycopg.Connection, customer_id: UUID) -> str | None:
    row = fetch_one(conn, "SELECT name FROM customer WHERE id = %s", (customer_id,))
    return None if row is None else row["name"]
