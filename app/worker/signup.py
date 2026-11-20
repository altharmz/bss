"""Online signup: what the worker does for a signup (docs/decisions/self-care.md "Online signup").

Each tick, after the prepaid commands (so a top-up the gateway confirmed in this tick is seen):

1. signups without progress for 24 hours before payment expire;
2. the identity checks are sent through the adapter (app/integrations/identity) or asked again:
   passed, failed, review (an operator decides), or a temporary error retried with backoff; an
   exhausted check sends the signup to an operator for a decision;
3. the first top-ups the gateway answered: confirmed -> `paid`, declined -> the signup stays
   `identityPassed` with the reason, to pay again;
4. `paid` signups get their TMF622 order: the plan with an eSIM from stock and a number from
   the pool, as an operator activation does -> `activating`; a refusal fails the signup (the
   top-up stays on the account as balance);
5. `activating` signups follow their order: completed -> `active` and the activation code is
   emailed; failed -> `failed`.

Each step locks the signup and re-reads its state, so a second worker or a second tick never
does a step twice.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg

from app.api import ordering as tmf_ordering
from app.care.signup import activation_code, esim_of, number_of
from app.clock import Clock
from app.config import settings
from app.events import emit
from app.integrations.identity import IdentityTemporary, adapter
from app.integrations.identity.interface import Person
from app.integrations.notify import NotifyError
from app.integrations.notify import adapter as sender
from app.ordering import numbers as number_orders
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import notifications as messages
from app.repository import orders
from app.repository import payment_commands as commands
from app.repository import signups as repo
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

VERDICT = {"pass": "passed", "fail": "failed", "review": "review"}
SIGNUP_STATE = {"passed": "identityPassed", "failed": "identityFailed", "review": "identityReview"}


def process_signups(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """One pass; returns the ids of the signups acted on."""
    ensure_ownership(conn)
    acted: list[UUID] = []
    steps = (
        (repo.due_to_expire, _expire),
        (lambda c, now: repo.checks_due(c, now), _check),
        (lambda c, now: repo.paying(c), _payment),
        (lambda c, now: repo.in_state(c, "paid"), _order),
        (lambda c, now: repo.in_state(c, "activating"), _activation),
    )
    for find, step in steps:
        for found in find(conn, clock.now()):
            if checkpoint is not None and checkpoint():
                return acted
            now = clock.now()
            with conn.transaction():
                uid = step(conn, found["id"], now, tz)
            if uid is not None:
                acted.append(uid)
                if step in (_order, _activation):  # the step that made it active, once
                    _email_activation(conn, uid, now)
            if heartbeat is not None:
                heartbeat()
    return acted


def _expire(conn: psycopg.Connection, uid: UUID, now: datetime, tz) -> UUID | None:
    row = repo.get(conn, uid, lock=True)
    if row is None or not repo.expired(row, now):
        return None
    repo.update(conn, uid, now, state="expired")
    return uid


# --- identity ---


def _check(conn: psycopg.Connection, check_id: UUID, now: datetime, tz) -> UUID | None:
    check = repo.get_check(conn, check_id, lock=True)
    if check is None or check["state"] not in ("pending", "sent"):
        return None
    signup = repo.get(conn, check["care_signup_id"], lock=True)
    request = check["request"]
    person = Person(
        request["givenName"],
        request["familyName"],
        datetime.fromisoformat(request["birthDate"]).date(),
    )
    try:
        result = adapter().verify(str(check_id), person, request["address"])
    except Exception as error:  # noqa: BLE001 - temporary or unknown: send again later, same key
        if not isinstance(error, IdentityTemporary):
            log.exception("identity check %s", check_id)
        check = repo.check_temporary(
            conn,
            check_id,
            error=str(error) or type(error).__name__,
            max_attempts=settings.identity_max_attempts,
            now=now,
        )
        if check["state"] == "exhausted" and signup["state"] == "identityPending":
            repo.update(
                conn,
                signup["id"],
                now,
                state="identityReview",
                identity_reason=f"the identity check could not be done: {check['last_error']}",
            )
        return signup["id"]
    if result is None:
        if check["state"] == "pending":
            repo.check_sent(conn, check_id, now)
        return None
    state = VERDICT[result.verdict]
    repo.check_decided(conn, check_id, state, result.reason, now)
    if signup["state"] == "identityPending" and signup["identity_check_id"] == check_id:
        repo.update(
            conn, signup["id"], now, state=SIGNUP_STATE[state], identity_reason=result.reason
        )
    return signup["id"]


# --- the first top-up ---


def _payment(conn: psycopg.Connection, uid: UUID, now: datetime, tz) -> UUID | None:
    signup = repo.get(conn, uid, lock=True)
    if (
        signup is None
        or signup["state"] != "identityPassed"
        or signup["payment_command_id"] is None
    ):
        return None
    command = commands.get(conn, signup["payment_command_id"])
    if command["state"] == "confirmed":
        repo.update(conn, uid, now, state="paid")
        return uid
    if command["state"] in ("declined", "exhausted"):
        reason = command["decline_reason"] or command["last_error"] or "the card was not charged"
        repo.update(conn, uid, now, payment_command_id=None, payment_error=reason)
        return uid
    return None


# --- the order ---


def _fail(conn: psycopg.Connection, uid: UUID, now: datetime, code: str, reason: str) -> UUID:
    repo.update(conn, uid, now, state="failed", failure_code=code, failure_reason=reason)
    return uid


def _order(conn: psycopg.Connection, uid: UUID, now: datetime, tz) -> UUID | None:
    signup = repo.get(conn, uid, lock=True)
    if signup is None or signup["state"] != "paid":
        return None
    today = now.astimezone(tz).date()
    offering_id = signup["product_offering_id"]
    needs = number_orders.requirements(conn, offering_id)
    product: dict = {"@type": "ProductRefOrValue"}
    if needs["msisdn"]:
        number = repo.pool_number(conn, today, settings.msisdn_quarantine_days)
        if number is None:
            return _fail(conn, uid, now, "numberUnavailable", "no number is free in the pool")
        product["realizingResource"] = [
            {"id": str(number["id"]), "@type": "ResourceRef", "@referredType": "LogicalResource"}
        ]
    if needs["imsi"]:
        product["productCharacteristic"] = [
            {"@type": "StringCharacteristic", "name": "simType", "value": "esim"}
        ]
    request = OrderRequest(
        billing_account_id=signup["billing_account_id"],
        items=(
            ItemRequest(
                item_id="1",
                action="add",
                product_offering_id=offering_id,
                billing_account_id=signup["billing_account_id"],
                details={"product": product},
            ),
        ),
        order_id=uuid4(),
    )
    try:
        order_id = place_order(conn, request, now=now, today=today)
    except OrderRefused as exc:
        return _fail(conn, uid, now, "orderRefused", str(exc))
    order = orders.get_order(conn, order_id)
    assert order is not None
    tmf = tmf_ordering.order_body(conn, order)
    emit.created(conn, tmf_ordering.API, "productOrder", tmf, at=now)
    product_id = order["items"][0]["product_id"] if order["items"] else None
    repo.update(
        conn, uid, now, product_order_id=order_id, product_id=product_id, state="activating"
    )
    if order["state"] in ("rejected", "failed"):
        return _order_failed(conn, uid, now, tmf)
    if order["state"] == "completed":
        repo.update(conn, uid, now, state="active")
    return uid


def _order_failed(conn: psycopg.Connection, uid: UUID, now: datetime, tmf: dict) -> UUID:
    errors = tmf.get("productOrderErrorMessage") or []
    first = errors[0] if errors else {}
    return _fail(
        conn,
        uid,
        now,
        first.get("code") or "orderFailed",
        "; ".join(f"{e['code']}: {e.get('message') or e.get('reason')}" for e in errors)
        or "the order did not complete",
    )


def _activation(conn: psycopg.Connection, uid: UUID, now: datetime, tz) -> UUID | None:
    signup = repo.get(conn, uid, lock=True)
    if signup is None or signup["state"] != "activating":
        return None
    order = orders.get_order(conn, signup["product_order_id"])
    if order["state"] == "completed":
        repo.update(conn, uid, now, state="active")
        return uid
    if order["state"] in ("failed", "rejected", "cancelled"):
        return _order_failed(conn, uid, now, tmf_ordering.order_body(conn, order))
    return None


# --- the activation email ---

SUBJECT = "Your eSIM is ready"
TEXT = (
    "Hello {name},\n\nyour line {number} is active. Install the eSIM with this activation code"
    " (or scan it as a QR code): {code}\nKeep it private: anyone with it can install the eSIM."
)


def _email_activation(conn: psycopg.Connection, uid: UUID, now: datetime) -> None:
    """Email the activation code of a signup that just became active (once: the state moved
    in the same pass), and log the message with the code masked."""
    signup = repo.get(conn, uid)
    if signup is None or signup["state"] != "active":
        return
    sim = esim_of(conn, signup["product_id"])
    if sim is None:
        return
    params = {
        "name": signup["given_name"] or "customer",
        "number": number_of(conn, signup["product_id"]) or "",
    }
    message = uuid4()
    ref, error = None, None
    try:
        ref = sender().send_email(
            str(message), signup["email"], SUBJECT, TEXT.format(code=activation_code(sim), **params)
        )
    except NotifyError as exc:
        error = str(exc) or "the sender refused the message"
    except Exception as exc:  # noqa: BLE001 - GET shows the code once; the log keeps why
        log.exception("signup activation email %s", message)
        error = str(exc) or type(exc).__name__
    with conn.transaction():
        messages.record_sent(
            conn,
            uid=message,
            event_type="care_esim_ready",
            receiver_name=signup["given_name"],
            receiver_email=signup["email"],
            customer_id=signup["customer_id"],
            subject=SUBJECT,
            content=TEXT.format(code="******", **params),
            ref=ref,
            error=error,
            now=now,
        )
