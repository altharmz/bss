"""TMF654 balance actions: the top-up lifecycle over `balance_action` (migration 0045).

Every writer here runs under the prepaid account's row lock (`billing.load_account`). The
combined-bounds check of 0045 is deferred, so a caller that wants a 409 instead of a
commit-time failure calls `check_bounds` after writing.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from app.repository import billing, funding
from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning


def bucket(conn: psycopg.Connection, bucket_id: UUID) -> Row | None:
    """The bucket with its account's state; the account is not locked."""
    return fetch_one(
        conn,
        "SELECT b.id, b.billing_account_id, b.currency, a.state::text AS account_state,"
        " a.name AS account_name FROM prepaid_bucket b"
        " JOIN billing_account a ON a.id = b.billing_account_id WHERE b.id = %s",
        (bucket_id,),
    )


def account_bucket(conn: psycopg.Connection, account_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT id, billing_account_id, currency FROM prepaid_bucket WHERE billing_account_id = %s",
        (account_id,),
    )


def insert_topup(
    conn: psycopg.Connection,
    bucket: Row,
    *,
    request: dict[str, Any],
    amount: Decimal,
    operator: str,
    now: datetime,
    reason: str | None,
    description: str | None,
    payment_id: UUID | None = None,
    failure: str | None = None,
) -> Row:
    """A completed top-up recording `payment_id`, or a failed one (`failure`, no payment)."""
    return returning(
        conn,
        "INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id,"
        " request, amount, reason, description, failure_reason, operator, requested_at,"
        " confirmed_at, payment_id)"
        " VALUES ('topup', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            "failed" if failure else "completed",
            bucket["billing_account_id"],
            bucket["currency"],
            bucket["id"],
            jsonb(request),
            amount,
            reason,
            description,
            failure,
            operator,
            now,
            now,
            payment_id,
        ),
    )


def get(
    conn: psycopg.Connection, action_id: UUID, *, lock: bool = False, kind: str | None = "topup"
) -> Row | None:
    """The action; of `kind`, or of any kind when `kind` is None (the history)."""
    query = "SELECT * FROM balance_action WHERE id = %s AND (%s::text IS NULL OR kind = %s)"
    return fetch_one(conn, query + (" FOR UPDATE" if lock else ""), (action_id, kind, kind))


def page(
    conn: psycopg.Connection,
    where: dict[str, Any],
    offset: int,
    limit: int,
    *,
    kind: str | None = "topup",
) -> tuple[list[Row], bool]:
    """Actions of `kind` (any kind when None) matching every `column = value` of `where`.
    A kind's list (the TMF654 topup, adjust, reserve and transfer lists) is newest
    requested first (#314, decided by the human 2026-11-11); the history of every kind
    stays oldest first."""
    conditions = [sql.SQL("{}::text = %s").format(sql.Identifier(c)) for c in where]
    params = [str(value) for value in where.values()]
    if kind is not None:
        conditions.insert(0, sql.SQL("kind = %s"))
        params.insert(0, kind)
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM balance_action")
        + clause
        + sql.SQL(
            " ORDER BY requested_at DESC, id DESC OFFSET %s LIMIT %s"
            if kind is not None
            else " ORDER BY requested_at, id OFFSET %s LIMIT %s"
        ),
        [*params, offset, limit + 1],
    )
    return rows[:limit], len(rows) > limit


def set_descriptive(
    conn: psycopg.Connection, action_id: UUID, changes: dict[str, Any], *, kind: str = "topup"
) -> Row:
    """Edit `reason` and `description`, the only columns a PATCH may change besides status."""
    if not changes:
        row = get(conn, action_id, kind=kind)
        assert row is not None
        return row
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
    )
    return returning(
        conn,
        sql.SQL("UPDATE balance_action SET {} WHERE id = %s RETURNING *").format(assignments),
        [*changes.values(), action_id],
    )


def payment_in_use(conn: psycopg.Connection, payment_id: UUID) -> Decimal:
    """What of a payment is allocated to bills, paid out, held or consumed."""
    row = fetch_one(
        conn,
        "SELECT (SELECT coalesce(sum(amount), 0) FROM bill_allocation WHERE payment_id = %(p)s)"
        " + (SELECT coalesce(sum(amount), 0) FROM refund_source WHERE payment_id = %(p)s)"
        " + (SELECT coalesce(sum(s.amount), 0) FROM funding_hold_source s"
        "    JOIN funding_hold h ON h.id = s.hold_id"
        "    WHERE s.payment_id = %(p)s AND h.released_at IS NULL)"
        " + (SELECT coalesce(sum(amount), 0) FROM funding_consumption WHERE payment_id = %(p)s)"
        " AS used",
        {"p": payment_id},
    )
    assert row is not None
    return row["used"]


def cancel_topup(conn: psycopg.Connection, action: Row, *, operator: str, now: datetime) -> Row:
    """Append the compensating entries of a completed top-up and mark it cancelled. The
    payment itself is never touched; the caller has checked it is entirely unused."""
    conn.execute(
        "INSERT INTO balance_action_reversal"
        " (action_id, billing_account_id, currency, amount, operator, reversed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            action["id"],
            action["billing_account_id"],
            action["currency"],
            -action["amount"],
            operator,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO funding_consumption (billing_account_id, action_id, reversal_action_id,"
        " payment_id, amount, consumed_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            action["billing_account_id"],
            action["id"],
            action["id"],
            action["payment_id"],
            action["amount"],
            now,
        ),
    )
    return returning(
        conn,
        "UPDATE balance_action SET status = 'cancelled', cancelled_at = %s WHERE id = %s"
        " RETURNING *",
        (now, action["id"]),
    )


# The funding_consumption column that names a source of each kind.
SOURCE_COLUMN = {
    "payment": "payment_id",
    "creditNote": "credit_bill_id",
    "adjustment": "funding_source_id",
    "transfer": "funding_source_id",
    "reversal": "funding_source_id",
}


def insert_adjustment(
    conn: psycopg.Connection,
    bucket: Row,
    *,
    request: dict[str, Any],
    amount: Decimal,
    operator: str,
    now: datetime,
    reason: str | None,
    description: str | None,
    sources: tuple = (),
    failure: str | None = None,
) -> Row:
    """A completed signed adjustment, or a failed one (`failure`). A credit introduces one
    adjustment source, available now; a debit consumes exactly `sources` (the funding plan's
    SourceAmounts, summing to -amount)."""
    row = returning(
        conn,
        "INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id,"
        " request, amount, reason, description, failure_reason, operator, requested_at,"
        " confirmed_at)"
        " VALUES ('adjustment', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            "failed" if failure else "completed",
            bucket["billing_account_id"],
            bucket["currency"],
            bucket["id"],
            jsonb(request),
            amount,
            reason,
            description,
            failure,
            operator,
            now,
            now,
        ),
    )
    if failure:
        return row
    if amount > 0:
        conn.execute(
            "INSERT INTO funding_source (kind, billing_account_id, currency, action_id, amount,"
            " available_at) VALUES ('adjustment', %s, %s, %s, %s, %s)",
            (row["billing_account_id"], row["currency"], row["id"], amount, now),
        )
    for source in sources:
        conn.execute(
            sql.SQL(
                "INSERT INTO funding_consumption (billing_account_id, action_id, {}, amount,"
                " consumed_at) VALUES (%s, %s, %s, %s, %s)"
            ).format(sql.Identifier(SOURCE_COLUMN[source.kind])),
            (row["billing_account_id"], row["id"], source.id, source.amount, now),
        )
    return row


def cancel_adjustment(
    conn: psycopg.Connection, action: Row, *, operator: str, now: datetime
) -> Row:
    """Append the compensating entries of a completed adjustment and mark it cancelled. A
    credit's reversal consumes its own source (the caller then runs `check_bounds`, which
    refuses it once spent or held); a debit's restores every source it consumed, once, as
    a `reversal` source each."""
    conn.execute(
        "INSERT INTO balance_action_reversal"
        " (action_id, billing_account_id, currency, amount, operator, reversed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            action["id"],
            action["billing_account_id"],
            action["currency"],
            -action["amount"],
            operator,
            now,
        ),
    )
    if action["amount"] > 0:
        conn.execute(
            "INSERT INTO funding_consumption (billing_account_id, action_id, reversal_action_id,"
            " funding_source_id, amount, consumed_at)"
            " SELECT billing_account_id, action_id, action_id, id, amount, %s"
            " FROM funding_source WHERE action_id = %s AND kind = 'adjustment'",
            (now, action["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO funding_source (kind, billing_account_id, currency, action_id,"
            " consumption_id, amount, available_at)"
            " SELECT 'reversal', billing_account_id, %s, action_id, id, amount, %s"
            " FROM funding_consumption WHERE action_id = %s AND reversal_action_id IS NULL",
            (action["currency"], now, action["id"]),
        )
    return returning(
        conn,
        "UPDATE balance_action SET status = 'cancelled', cancelled_at = %s WHERE id = %s"
        " RETURNING *",
        (now, action["id"]),
    )


def insert_transfer(
    conn: psycopg.Connection,
    bucket: Row,
    receiver: Row,
    *,
    action_id: UUID,
    request: dict[str, Any],
    amount: Decimal,
    cost: Decimal,
    cost_owner: str,
    operator: str,
    now: datetime,
    reason: str | None,
    description: str | None,
    hold_id: UUID | None = None,
    command_id: UUID | None = None,
    failure: str | None = None,
) -> Row:
    """An accepted transfer (`created`: its principal hold and worker command, nothing
    moved yet) or a refused one (`failure`, neither). `receiver` is the receiving bucket."""
    return returning(
        conn,
        "INSERT INTO balance_action (id, kind, status, billing_account_id, currency, bucket_id,"
        " request, amount, transfer_cost, cost_owner, receiver_bucket_id,"
        " receiver_billing_account_id, receiver_currency, reason, description, failure_reason,"
        " operator, requested_at, confirmed_at, hold_id, command_id)"
        " VALUES (%s, 'transfer', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s) RETURNING *",
        (
            action_id,
            "failed" if failure else "created",
            bucket["billing_account_id"],
            bucket["currency"],
            bucket["id"],
            jsonb(request),
            amount,
            cost,
            cost_owner,
            receiver["id"],
            receiver["billing_account_id"],
            receiver["currency"],
            reason,
            description,
            failure,
            operator,
            now,
            now if failure else None,
            hold_id,
            command_id,
        ),
    )


def complete_transfer(conn: psycopg.Connection, action_id: UUID, *, now: datetime) -> Row:
    """The worker moved the principal: created -> completed."""
    return returning(
        conn,
        "UPDATE balance_action SET status = 'completed', confirmed_at = %s WHERE id = %s"
        " AND kind = 'transfer' AND status = 'created' RETURNING *",
        (now, action_id),
    )


def fail_transfer(conn: psycopg.Connection, action_id: UUID, reason: str, *, now: datetime) -> Row:
    """Revalidation refused an accepted transfer: created -> failed, its hold already released."""
    return returning(
        conn,
        "UPDATE balance_action SET status = 'failed', failure_reason = %s, confirmed_at = %s"
        " WHERE id = %s AND kind = 'transfer' AND status = 'created' RETURNING *",
        (reason, now, action_id),
    )


def delete_failed(conn: psycopg.Connection, action_id: UUID) -> None:
    conn.execute("DELETE FROM balance_action WHERE id = %s AND status = 'failed'", (action_id,))


def check_bounds(conn: psycopg.Connection) -> None:
    """Run the deferred ledger checks now (psycopg.errors.CheckViolation when broken)."""
    conn.execute("SET CONSTRAINTS balance_ledger_bounds IMMEDIATE")


# --- operator holds (reserveBalance) ---------------------------------------------------


def expiry(request: dict[str, Any]) -> datetime | None:
    """The instant an operator hold lapses: the request's `validFor.endDateTime`, as sent
    (a zone-less one is UTC). The API accepts only values this parses."""
    end = (request.get("validFor") or {}).get("endDateTime")
    if not end:
        return None
    at = datetime.fromisoformat(end)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def insert_reservation(
    conn: psycopg.Connection,
    bucket: Row,
    *,
    request: dict[str, Any],
    amount: Decimal,
    operator: str,
    now: datetime,
    reason: str | None,
    description: str | None,
    hold_id: UUID | None = None,
    failure: str | None = None,
) -> Row:
    """A created operator hold on `hold_id`, or a failed one (`failure`, no hold)."""
    return returning(
        conn,
        "INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id,"
        " request, amount, reason, description, failure_reason, operator, requested_at,"
        " confirmed_at, hold_id)"
        " VALUES ('reservation', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING *",
        (
            "failed" if failure else "created",
            bucket["billing_account_id"],
            bucket["currency"],
            bucket["id"],
            jsonb(request),
            amount,
            reason,
            description,
            failure,
            operator,
            now,
            now if failure else None,
            hold_id,
        ),
    )


def cancel_reservation(conn: psycopg.Connection, action: Row, *, now: datetime) -> Row:
    """Release a created operator hold and mark it cancelled, under the account lock."""
    funding.release(conn, action["billing_account_id"], action["hold_id"], released_at=now)
    return returning(
        conn,
        "UPDATE balance_action SET status = 'cancelled', cancelled_at = %s WHERE id = %s"
        " RETURNING *",
        (now, action["id"]),
    )


def _lapsed(conn: psycopg.Connection, now: datetime, account_id: UUID | None) -> list[Row]:
    rows = fetch_all(
        conn,
        "SELECT * FROM balance_action WHERE kind = 'reservation' AND status = 'created'"
        " AND command_id IS NULL AND (%(a)s::uuid IS NULL OR billing_account_id = %(a)s)"
        " ORDER BY requested_at, id",
        {"a": account_id},
    )
    return [r for r in rows if (end := expiry(r["request"])) is not None and end <= now]


def release_expired(conn: psycopg.Connection, account_id: UUID, now: datetime) -> list[Row]:
    """Release the account's operator holds whose end has passed (half-open: at the end
    instant it has lapsed), each once, sending a Cancel event. Call under the account's
    row lock, before anything that spends: a lapsed hold must not keep money back."""
    from app.events import emit  # emit imports no API at load time; the body is built lazily

    released = []
    for action in _lapsed(conn, now, account_id):
        row = cancel_reservation(conn, action, now=now)

        def build(row=row) -> dict[str, Any]:
            from app.api.reserve import reserve_body

            return reserve_body(conn, row)

        emit.emit(conn, "TMF654", "reserveBalance", "Cancel", build, at=now)
        released.append(row)
    return released


def release_all_expired(conn: psycopg.Connection, now: datetime) -> int:
    """The worker's sweep: release every lapsed operator hold, one account transaction
    each. Returns how many were released."""
    count = 0
    for account_id in dict.fromkeys(r["billing_account_id"] for r in _lapsed(conn, now, None)):
        with conn.transaction():
            billing.load_account(conn, account_id)
            count += len(release_expired(conn, account_id, now))
    return count
