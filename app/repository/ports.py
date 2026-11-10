"""Port requests and Number Transfer PINs (docs/decisions/numbers.md)."""

import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, returning

NAMESPACE = UUID("8c1f6a3e-52d9-5b47-a0c8-3e7d91b4f206")
STATES = ("submitted", "pending", "rejected", "completed", "cancelled")
DIRECTIONS = ("in", "out")
OPEN = ("submitted", "pending", "rejected")


def submission_key(request: Row) -> str:
    """The idempotency key of this submission: a resubmit is a new one."""
    return str(uuid5(NAMESPACE, f"{request['id']}:{request['submissions']}"))


def answer_key(request: Row) -> str:
    return str(uuid5(NAMESPACE, f"{request['id']}:answer"))


def get(conn: psycopg.Connection, uid: UUID, *, lock: bool = False) -> Row | None:
    query = "SELECT * FROM port_request WHERE id = %s"
    return fetch_one(conn, query + " FOR UPDATE" if lock else query, (uid,))


def insert_in(
    conn: psycopg.Connection,
    *,
    value: str,
    number_id: UUID,
    carrier_account_number: str,
    billing_zip: str,
    first_name: str,
    last_name: str,
    transfer_pin: str,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "INSERT INTO port_request (id, direction, state, number_value, subscriber_number_id,"
        " carrier_account_number, billing_zip, first_name, last_name, transfer_pin,"
        " created_at, updated_at)"
        " VALUES (%s, 'in', 'submitted', %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING *",
        (
            uuid4(),
            value,
            number_id,
            carrier_account_number,
            billing_zip,
            first_name,
            last_name,
            transfer_pin,
            now,
            now,
        ),
    )


def insert_out(
    conn: psycopg.Connection,
    *,
    reference: str,
    value: str,
    number_id: UUID | None,
    product_id: UUID | None,
    account_id: UUID | None,
    now: datetime,
) -> Row | None:
    """Record a port-out the carrier reported (completed or rejected right after);
    None when this reference was recorded before."""
    return fetch_one(
        conn,
        "INSERT INTO port_request (id, direction, state, number_value, subscriber_number_id,"
        " carrier_reference, product_id, billing_account_id, created_at, updated_at)"
        " VALUES (%s, 'out', 'pending', %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (carrier_reference) WHERE direction = 'out' DO NOTHING RETURNING *",
        (uuid4(), value, number_id, reference, product_id, account_id, now, now),
    )


def decide_out(
    conn: psycopg.Connection,
    uid: UUID,
    *,
    approved: bool,
    code: str | None,
    reason: str | None,
    order_id: UUID | None,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET state = %s, rejection_code = %s, rejection_reason = %s,"
        " product_order_id = %s, completed_at = %s, updated_at = %s WHERE id = %s RETURNING *",
        (
            "completed" if approved else "rejected",
            code,
            reason,
            order_id,
            now if approved else None,
            now,
            uid,
        ),
    )


def unanswered(conn: psycopg.Connection) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM port_request WHERE direction = 'out' AND state IN ('completed', 'rejected')"
        " AND answered_at IS NULL ORDER BY created_at, id",
    )


def mark_answered(conn: psycopg.Connection, uid: UUID, now: datetime) -> None:
    conn.execute("UPDATE port_request SET answered_at = %s WHERE id = %s", (now, uid))


def to_submit(conn: psycopg.Connection, now: datetime) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM port_request WHERE direction = 'in' AND state = 'submitted'"
        " AND (next_attempt_at IS NULL OR next_attempt_at <= %s) ORDER BY created_at, id",
        (now,),
    )


def to_poll(conn: psycopg.Connection) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM port_request WHERE direction = 'in' AND state = 'pending'"
        " ORDER BY created_at, id",
    )


def accepted(conn: psycopg.Connection, uid: UUID, reference: str, now: datetime) -> Row:
    """The carrier has the submission: pending; our copy of the PIN is cleared."""
    return returning(
        conn,
        "UPDATE port_request SET state = 'pending', carrier_reference = %s, transfer_pin = NULL,"
        " attempts = 0, next_attempt_at = NULL, last_error = NULL, updated_at = %s"
        " WHERE id = %s RETURNING *",
        (reference, now, uid),
    )


def reject(conn: psycopg.Connection, uid: UUID, *, code: str, reason: str, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET state = 'rejected', rejection_code = %s, rejection_reason = %s,"
        " transfer_pin = NULL, next_attempt_at = NULL, updated_at = %s WHERE id = %s RETURNING *",
        (code, reason, now, uid),
    )


def complete(conn: psycopg.Connection, uid: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET state = 'completed', completed_at = %s, updated_at = %s,"
        " rejection_code = NULL, rejection_reason = NULL WHERE id = %s RETURNING *",
        (now, now, uid),
    )


def temporary(conn: psycopg.Connection, uid: UUID, *, error: str, wait, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET attempts = attempts + 1, last_error = %s, next_attempt_at = %s,"
        " updated_at = %s WHERE id = %s RETURNING *",
        (error, now + wait, now, uid),
    )


def resubmit(conn: psycopg.Connection, uid: UUID, values: dict[str, Any], now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET state = 'submitted', submissions = submissions + 1,"
        " rejection_code = NULL, rejection_reason = NULL, attempts = 0, next_attempt_at = NULL,"
        " last_error = NULL, carrier_account_number = %(account)s, billing_zip = %(zip)s,"
        " first_name = %(first)s, last_name = %(last)s, transfer_pin = %(pin)s,"
        " updated_at = %(now)s WHERE id = %(id)s RETURNING *",
        values | {"id": uid, "now": now},
    )


def cancel(conn: psycopg.Connection, uid: UUID, now: datetime) -> Row:
    return returning(
        conn,
        "UPDATE port_request SET state = 'cancelled', transfer_pin = NULL, next_attempt_at = NULL,"
        " updated_at = %s WHERE id = %s RETURNING *",
        (now, uid),
    )


def cancel_for_order(conn: psycopg.Connection, order_id: UUID, now: datetime) -> None:
    """A cancelled order withdraws the port-ins of the numbers it claimed."""
    conn.execute(
        "UPDATE port_request SET state = 'cancelled', transfer_pin = NULL, next_attempt_at = NULL,"
        " updated_at = %s WHERE direction = 'in' AND state IN ('submitted', 'pending', 'rejected')"
        " AND subscriber_number_id IN (SELECT a.subscriber_number_id FROM number_assignment a"
        " JOIN product_order_item i ON i.id = a.assigned_by_item_id"
        " WHERE i.product_order_id = %s)",
        (now, order_id),
    )


def open_for_number(conn: psycopg.Connection, number_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM port_request WHERE direction = 'in' AND subscriber_number_id = %s"
        " AND state IN ('submitted', 'pending', 'rejected', 'completed')"
        " ORDER BY created_at DESC LIMIT 1",
        (number_id,),
    )


def page(
    conn: psycopg.Connection,
    *,
    direction: str | None,
    state: str | None,
    number: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    where = (  # noqa: S608 - fixed text, the values are parameters
        " FROM port_request WHERE (%(d)s::text IS NULL OR direction = %(d)s)"
        " AND (%(s)s::text IS NULL OR state = %(s)s)"
        " AND (%(n)s::text IS NULL OR number_value = %(n)s)"
    )
    args = {"d": direction, "s": state, "n": number, "off": offset, "lim": limit + 1}
    rows = fetch_all(
        conn,
        f"SELECT *{where} ORDER BY created_at DESC, id LIMIT %(lim)s OFFSET %(off)s",  # noqa: S608
        args,
    )
    return rows[:limit], len(rows) > limit


def claimed(conn: psycopg.Connection, number_id: UUID) -> bool:
    """An order holds (or held and not cancelled) a claim on the number."""
    return (
        fetch_one(
            conn,
            "SELECT 1 FROM number_assignment WHERE subscriber_number_id = %s"
            " AND cancelled_at IS NULL AND NOT isempty(valid_for) LIMIT 1",
            (number_id,),
        )
        is not None
    )


# --- Number Transfer PINs ---


def pin_hash(pin_id: UUID, pin: str) -> str:
    return hashlib.sha256(f"{pin_id}:{pin}".encode()).hexdigest()


def insert_pin(
    conn: psycopg.Connection,
    *,
    pin_id: UUID,
    number_id: UUID,
    product_id: UUID,
    pin: str,
    now: datetime,
    expires_at: datetime,
) -> Row:
    conn.execute(
        "UPDATE number_transfer_pin SET revoked_at = %s WHERE product_id = %s"
        " AND revoked_at IS NULL AND used_at IS NULL",
        (now, product_id),
    )
    return returning(
        conn,
        "INSERT INTO number_transfer_pin (id, subscriber_number_id, product_id, pin_hash,"
        " created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (pin_id, number_id, product_id, pin_hash(pin_id, pin), now, expires_at),
    )


def pins_of(conn: psycopg.Connection, product_id: UUID) -> list[Row]:
    return fetch_all(
        conn,
        "SELECT * FROM number_transfer_pin WHERE product_id = %s ORDER BY created_at DESC, id",
        (product_id,),
    )


def match_pin(
    conn: psycopg.Connection, product_id: UUID, number_id: UUID, pin: str
) -> tuple[Row | None, str]:
    """The line's current PIN when `pin` is it: (row, `ok`), or (row, `used`) once used;
    else (None, `invalid`). A revoked PIN never matches; expiry is the caller's check."""
    now_row = fetch_one(
        conn,
        "SELECT * FROM number_transfer_pin WHERE product_id = %s AND subscriber_number_id = %s"
        " AND revoked_at IS NULL ORDER BY created_at DESC, id LIMIT 1 FOR UPDATE",
        (product_id, number_id),
    )
    if now_row is None or now_row["pin_hash"] != pin_hash(now_row["id"], pin):
        return None, "invalid"
    if now_row["used_at"] is not None:
        return now_row, "used"
    return now_row, "ok"


def use_pin(conn: psycopg.Connection, uid: UUID, now: datetime) -> None:
    conn.execute("UPDATE number_transfer_pin SET used_at = %s WHERE id = %s", (now, uid))
