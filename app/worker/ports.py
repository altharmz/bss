"""Number porting with the carrier (docs/decisions/numbers.md).

Each tick: our answers to port-outs the carrier has not acknowledged are resent; the
port-outs the carrier reports are validated and decided; due port-ins are submitted and the
port-ins that were already `pending` at the start of the tick are read (the stub therefore
completes on the next wake-up). A port-in that completes lets the line's `activate`
network command go (app/repository/network_commands.py `due`).
"""

import logging
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.config import settings
from app.integrations.network import NetworkRefused, NetworkTemporary, PortOutRequest, adapter
from app.ordering import numbers as number_orders
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import network_commands as network
from app.repository import orders, ports, products, security
from app.repository._sql import Row, fetch_one
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


class _Refused(Exception):
    def __init__(self, code: str, reason: str, product: Row | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.product = product  # the line, when the refusal is about a protected one


def process_ports(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> None:
    ensure_ownership(conn)
    now = clock.now()
    today = now.astimezone(tz).date()
    polling = [row["id"] for row in ports.to_poll(conn)]
    try:
        reported = adapter().port_out_requests()
    except Exception:  # noqa: BLE001 - the carrier is unreachable: try on the next tick
        log.exception("port-out requests")
        reported = []
    for request in reported:
        if checkpoint is not None and checkpoint():
            return
        with conn.transaction():
            register_port_out(conn, request, now=now, today=today)
        if heartbeat is not None:
            heartbeat()
    for row in ports.unanswered(conn):
        answer_port_out(conn, row, now=now)
    for row in ports.to_submit(conn, now):
        if checkpoint is not None and checkpoint():
            return
        with conn.transaction():
            submit_port_in(conn, row["id"], now=now)
        if heartbeat is not None:
            heartbeat()
    for uid in polling:
        if checkpoint is not None and checkpoint():
            return
        with conn.transaction():
            poll_port_in(conn, uid, now=now)
        if heartbeat is not None:
            heartbeat()


# --- port-in ---


def _request(row: Row) -> dict:
    return {
        "msisdn": row["number_value"],
        "carrierAccountNumber": row["carrier_account_number"],
        "transferPin": row["transfer_pin"],
        "billingZip": row["billing_zip"],
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "submission": row["submissions"],
    }


def _apply(conn, row: Row, status, now: datetime) -> None:
    if status.state == "completed":
        ports.complete(conn, row["id"], now)
    elif status.state == "rejected":
        ports.reject(
            conn,
            row["id"],
            code=status.code or "rejected",
            reason=status.reason or "the carrier rejected the port",
            now=now,
        )
    elif row["state"] == "submitted":
        ports.accepted(conn, row["id"], status.reference or "", now)


def submit_port_in(conn: psycopg.Connection, uid: UUID, *, now: datetime) -> None:
    row = ports.get(conn, uid, lock=True)
    if row["state"] != "submitted":
        return
    try:
        status = adapter().submit_port_in(ports.submission_key(row), _request(row))
    except NetworkRefused as refused:
        ports.reject(conn, uid, code=refused.code, reason=refused.reason, now=now)
    except Exception as error:  # noqa: BLE001 - NetworkTemporary or unknown: send again later
        if not isinstance(error, NetworkTemporary):
            log.exception("port-in %s", uid)
        message = str(error) or type(error).__name__
        if row["attempts"] + 1 >= settings.network_max_attempts:
            ports.reject(
                conn,
                uid,
                code="networkUnavailable",
                reason=f"the carrier could not be reached: {message}",
                now=now,
            )
        else:
            ports.temporary(
                conn,
                uid,
                error=message,
                wait=network.BACKOFF[min(row["attempts"], len(network.BACKOFF) - 1)],
                now=now,
            )
    else:
        _apply(conn, row, status, now)


def poll_port_in(conn: psycopg.Connection, uid: UUID, *, now: datetime) -> None:
    row = ports.get(conn, uid, lock=True)
    if row["state"] != "pending":
        return
    try:
        status = adapter().port_in_status(row["carrier_reference"])
    except Exception:  # noqa: BLE001 - read again on the next tick
        log.exception("port-in status %s", uid)
        return
    _apply(conn, row, status, now)


# --- port-out ---


def _decide(conn, request: PortOutRequest, now: datetime, today: date):
    """(number, product, pin) of a valid request, or `_Refused`."""
    number = fetch_one(
        conn,
        "SELECT * FROM subscriber_number WHERE resource_specification_id = 'msisdn'"
        " AND value = %s FOR UPDATE",
        (request.msisdn,),
    )
    if number is None:
        raise _Refused("numberUnknown", "the number is not in this BSS")
    claim = fetch_one(
        conn,
        "SELECT product_id FROM number_assignment WHERE subscriber_number_id = %s"
        " AND cancelled_at IS NULL AND valid_for @> %s::date",
        (number["id"], today),
    )
    product = None
    if claim is not None:
        product = products.lock_products(conn, [claim["product_id"]]).get(claim["product_id"])
    if (
        product is None
        or product["status"] not in ("active", "suspended")
        or product["termination_date"] is not None
    ):
        raise _Refused("lineNotActive", "the number is not on an active line")
    if (lock := security.lock_of(conn, product["id"])) is not None and lock["port_out"]:
        raise _Refused("portOutLocked", "the line has a port-out lock", product)
    pin, outcome = ports.match_pin(conn, product["id"], number["id"], request.pin)
    if pin is None:
        raise _Refused("pinInvalid", "the Number Transfer PIN does not match")
    if outcome == "used":
        raise _Refused("pinInvalid", "the Number Transfer PIN was already used")
    if pin["expires_at"] <= now:
        raise _Refused("pinExpired", "the Number Transfer PIN has expired")
    return number, product, pin


class _Rollback(Exception):
    pass


def register_port_out(
    conn: psycopg.Connection, request: PortOutRequest, *, now: datetime, today: date
) -> Row | None:
    """Record a port-out the carrier reported and decide it in this transaction: valid ->
    the PIN is used, a termination order ends the product (no network command), the number
    is released and locked; refused -> the reason is kept. Either way the carrier is answered
    by `answer_port_out` until it acknowledges."""
    number = fetch_one(
        conn,
        "SELECT id FROM subscriber_number WHERE resource_specification_id = 'msisdn'"
        " AND value = %s",
        (request.msisdn,),
    )
    row = ports.insert_out(
        conn,
        reference=request.reference,
        value=request.msisdn,
        number_id=None if number is None else number["id"],
        product_id=None,
        account_id=None,
        now=now,
    )
    if row is None:
        return None
    try:
        try:
            with conn.transaction():
                found, product, pin = _decide(conn, request, now, today)
                order_id = _terminate(conn, product, now, today)
                ports.use_pin(conn, pin["id"], now)
                _lock_number(conn, found, now, today)
        except _Rollback as rolled:
            raise _Refused("terminationRefused", str(rolled)) from None
    except _Refused as refused:
        if refused.product is not None:
            _audit(conn, refused.product, "refused", now, row, reason=refused.code)
        return ports.decide_out(
            conn,
            row["id"],
            approved=False,
            code=refused.code,
            reason=refused.reason,
            order_id=None,
            now=now,
        )
    conn.execute(
        "UPDATE port_request SET product_id = %s, billing_account_id = %s WHERE id = %s",
        (product["id"], product["billing_account_id"], row["id"]),
    )
    _audit(conn, product, "done", now, row)
    return ports.decide_out(
        conn, row["id"], approved=True, code=None, reason=None, order_id=order_id, now=now
    )


def _audit(conn, product: Row, outcome: str, now: datetime, row: Row, reason=None) -> None:
    """The carrier's port-out request, done or stopped by a lock, in the account audit log."""
    owner = security.customer_of_product(conn, product["id"])
    security.record(
        conn,
        now=now,
        operator="carrier",
        role=None,
        action="port_out",
        outcome=outcome,
        verification="none",
        reason=reason,
        customer_id=None if owner is None else owner["customer_id"],
        billing_account_id=product["billing_account_id"],
        product_id=product["id"],
        detail={"portRequest": str(row["id"]), "number": row["number_value"]},
    )


def _terminate(conn, product: Row, now: datetime, today: date) -> UUID:
    """End the product through an order dated today, without a network command."""
    order_id = place_order(
        conn,
        OrderRequest(
            billing_account_id=product["billing_account_id"],
            items=(ItemRequest(item_id="1", action="delete", product_id=product["id"]),),
            network=False,
        ),
        now=now,
        today=today,
    )
    order = orders.get_order(conn, order_id)
    if order["state"] == "rejected":
        errors = orders.error_messages(conn, order_id)
        raise _Rollback("; ".join(e["reason"] for e in errors) or "the termination was refused")
    return order_id


def _lock_number(conn, number: Row, now: datetime, today: date) -> None:
    """A ported-away number belongs to another carrier: it must never be assigned again."""
    locked = number_orders.lock(conn, [], [[{"id": str(number["id"])}]])
    old = number_orders.before(conn, locked, today)
    conn.execute(
        "UPDATE subscriber_number SET administrative_state = 'locked', updated_at = %s"
        " WHERE id = %s",
        (now, number["id"]),
    )
    number_orders.changed(conn, locked, old, now, today)


def answer_port_out(conn: psycopg.Connection, row: Row, *, now: datetime) -> None:
    try:
        adapter().answer_port_out(
            ports.answer_key(row),
            row["carrier_reference"],
            row["state"] == "completed",
            row["rejection_reason"],
        )
    except Exception:  # noqa: BLE001 - the carrier asks again; we answer on the next tick
        log.exception("port-out answer %s", row["id"])
        return
    with conn.transaction():
        ports.mark_answered(conn, row["id"], now)
