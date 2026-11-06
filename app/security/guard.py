"""Verification of the customer before an action that can take over a line (US CPNI practice).

The agent sends the customer's PIN in the `X-Account-Pin` header. A sensitive action on a
**protected** customer (one with a live prepaid product) needs a valid PIN; every attempt is
written to `account_audit` (done or refused, never the PIN) and a refusal is counted: at
`pin_max_attempts` wrong PINs in a row the verification locks for `pin_lockout_minutes`, even
for the right PIN. Locks per line (`line_lock`) refuse a SIM swap or a port-out whatever the PIN.

Functions take the caller's connection. A refusal is raised as `Refusal` *after* its audit entry
and counter were written: a handler that turns it into an error must commit them first (see
`app/api/guards.py`, the order engine records the refusal in the rejected order's transaction).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import psycopg

from app.config import settings
from app.repository import security as repo
from bss_mcp.passwords import check_password, hash_password

PIN_HEADER = "x-account-pin"
PIN_FORMAT = re.compile(r"^[0-9]{6,8}$")

MESSAGES = {
    "pinRequired": "this action needs the customer's account PIN (header X-Account-Pin)",
    "pinInvalid": "the account PIN is not valid",
    "pinNotSet": "the customer has no account PIN yet: an agent must set one first",
    "pinLocked": "too many wrong PINs: verification is locked, try again later",
    "simSwapLocked": "this line has a SIM swap lock: clear it first",
    "portOutLocked": "this line has a port-out lock: clear it first",
}


@dataclass(frozen=True)
class Actor:
    """Who acts, and the PIN they presented (None when they sent none)."""

    operator: str
    role: str | None = None
    pin: str | None = field(default=None, repr=False)  # never reaches a log by accident


class Refusal(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(MESSAGES[code])
        self.code = code

    def error(self):
        from app.api.errors import TmfError  # lazily: app.api imports the order engine

        return TmfError(403, MESSAGES[self.code], code=self.code)


def hash_pin(pin: str) -> str:
    return hash_password(pin)


def verify(conn: psycopg.Connection, customer_ids: list[UUID], actor: Actor, now: datetime) -> str:
    """'pin' (a valid PIN of one of the customers) or 'notRequired' (the switch is off);
    raises `Refusal`. Counts a wrong PIN and starts the lockout."""
    if not settings.account_pin_required:
        return "notRequired"
    rows = repo.pins_of(conn, customer_ids)
    if not rows:
        raise Refusal("pinNotSet")
    if actor.pin is None:
        raise Refusal("pinRequired")
    open_rows = [r for r in rows if r["locked_until"] is None or r["locked_until"] <= now]
    if not open_rows:
        raise Refusal("pinLocked")
    for row in open_rows:
        if check_password(actor.pin, row["pin_hash"]):
            repo.pin_ok(conn, row["customer_id"])
            return "pin"
    for row in open_rows:
        until = now + timedelta(minutes=settings.pin_lockout_minutes)
        after = repo.pin_failed(
            conn, row["customer_id"], maximum=settings.pin_max_attempts, until=until
        )
        if after["locked_until"] == until:
            repo.record(
                conn,
                now=now,
                operator=actor.operator,
                role=actor.role,
                action="pin_lockout",
                outcome="done",
                verification="none",
                customer_id=row["customer_id"],
                billing_account_id=repo.first_account(conn, row["customer_id"]),
                detail={"lockedUntil": until.isoformat(), "attempts": settings.pin_max_attempts},
            )
    raise Refusal("pinInvalid")


def check_lock(conn: psycopg.Connection, product_id: UUID, kind: str) -> None:
    """Raise `Refusal` when the line has the lock: kind is `sim_swap` or `port_out`."""
    row = repo.lock_of(conn, product_id)
    if row is not None and row[kind]:
        raise Refusal("simSwapLocked" if kind == "sim_swap" else "portOutLocked")


def gate(
    conn: psycopg.Connection,
    actor: Actor,
    now: datetime,
    *,
    action: str,
    customer_ids: list[UUID],
    product_id: UUID | None = None,
    account_id: UUID | None = None,
    lock: str | None = None,
    detail: dict | None = None,
) -> str:
    """Lock check, then PIN check, for `action`; a refusal writes its audit entry first.
    Returns the verification mode for the entry of the action when it is done."""
    customer = customer_ids[0] if len(customer_ids) == 1 else None
    try:
        if lock is not None and product_id is not None:
            check_lock(conn, product_id, lock)
        return verify(conn, customer_ids, actor, now)
    except Refusal as refusal:
        repo.record(
            conn,
            now=now,
            operator=actor.operator,
            role=actor.role,
            action=action,
            outcome="refused",
            reason=refusal.code,
            verification="none",
            customer_id=customer,
            billing_account_id=account_id,
            product_id=product_id,
            detail=detail,
        )
        raise


def done(
    conn: psycopg.Connection,
    actor: Actor,
    now: datetime,
    *,
    action: str,
    verification: str,
    customer_id: UUID | None,
    product_id: UUID | None = None,
    account_id: UUID | None = None,
    reason: str | None = None,
    detail: dict | None = None,
) -> None:
    repo.record(
        conn,
        now=now,
        operator=actor.operator,
        role=actor.role,
        action=action,
        outcome="done",
        verification=verification,
        reason=reason,
        customer_id=customer_id,
        billing_account_id=account_id,
        product_id=product_id,
        detail=detail,
    )
