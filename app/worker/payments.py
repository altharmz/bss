"""Send the card charges to the payment gateway and record what it answers.

Each tick: automatic top-ups are started for prepaid accounts below their threshold, due
charges are submitted through the adapter (app/integrations/payment), and the charges the
gateway already had at the start of the tick are read: confirmed (the TMF676 payment and
TMF654 top-up are recorded in one transaction, which is the only moment the balance
rises), or declined (nothing changes, the reason is kept). A temporary error is retried
with backoff and the command is `exhausted` after `payment_max_attempts`; an operator then
retries it. A resend carries the same key, so the gateway never charges twice.
docs/decisions/payments.md.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg import errors as pg_errors

from app.api.payment import PaymentCreate, post_payment
from app.api.payment_method import method_href
from app.api.topup import API as TOPUP_API
from app.api.topup import RESOURCE as TOPUP_RESOURCE
from app.api.topup import topup_body
from app.clock import Clock
from app.config import settings
from app.events import emit
from app.integrations.payment import PaymentDeclined, PaymentTemporary, adapter
from app.repository import balance_actions, funding, parties
from app.repository import payment_commands as commands
from app.repository import payment_methods as methods
from app.repository._sql import Row
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

OPERATOR = "system:payment-gateway"
REASON = {"topup": "Card top-up", "auto_topup": "Automatic card top-up"}


def charge_request(method: Row, amount: Decimal, currency: str, purpose: str) -> dict[str, Any]:
    """What is sent to the gateway, built once and kept; every retry resends it unchanged."""
    return {
        "token": method["gateway_token"],
        "amount": amount,
        "currency": currency,
        "paymentMethodId": str(method["id"]),
        "purpose": purpose,
    }


def usable(method: Row | None, now: datetime) -> str | None:
    """Why this card cannot be charged, or None."""
    if method is None or method["status"] != "active":
        return "the payment method does not exist or was removed"
    if method["expiration_date"] is not None and method["expiration_date"] <= now:
        return "the card has expired"
    return None


def process_payments(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """One pass; returns the ids of the commands acted on."""
    ensure_ownership(conn)
    now = clock.now()
    answered_before = [row["id"] for row in commands.awaiting(conn)]
    acted: list[UUID] = []
    start_auto_topups(conn, now=now)
    for due in commands.to_send(conn, now):
        if checkpoint is not None and checkpoint():
            return acted
        with conn.transaction():
            cmd = commands.get(conn, due["id"], lock=True)
            if cmd["state"] != "pending":
                continue
            send(conn, cmd, now)
        acted.append(due["id"])
        if heartbeat is not None:
            heartbeat()
    for uid in answered_before:
        if checkpoint is not None and checkpoint():
            return acted
        with conn.transaction():
            cmd = commands.get(conn, uid, lock=True)
            if cmd["state"] != "sent":
                continue
            if read_result(conn, cmd, now):
                acted.append(uid)
        if heartbeat is not None:
            heartbeat()
    return acted


def send(conn: psycopg.Connection, cmd: Row, now: datetime) -> None:
    request = cmd["request"]
    try:
        adapter().charge(
            str(cmd["id"]), request["token"], Decimal(str(request["amount"])), request["currency"]
        )
    except PaymentDeclined as declined:
        commands.decline(conn, cmd["id"], reason=declined.reason)
    except PaymentTemporary as error:
        _temporary(conn, cmd, str(error) or "temporary gateway error", now)
    except Exception as error:  # noqa: BLE001 - unknown outcome: send again later, same key
        log.exception("payment command %s", cmd["id"])
        _temporary(conn, cmd, str(error) or type(error).__name__, now)
    else:
        commands.mark_sent(conn, cmd["id"], now=now)


def _temporary(conn: psycopg.Connection, cmd: Row, error: str, now: datetime) -> None:
    commands.temporary(
        conn, cmd["id"], error=error, max_attempts=settings.payment_max_attempts, now=now
    )


def read_result(conn: psycopg.Connection, cmd: Row, now: datetime) -> bool:
    """Record the gateway's decision; False while it has none."""
    try:
        result = adapter().result(str(cmd["id"]))
    except Exception:  # noqa: BLE001 - read again on the next wake-up
        log.exception("payment command %s result", cmd["id"])
        return False
    if result is None:
        return False
    if result.status == "declined":
        commands.decline(conn, cmd["id"], reason=result.reason or "declined by the gateway")
    else:
        record_confirmed(conn, cmd, gateway_ref=result.gateway_ref or str(cmd["id"]), now=now)
    return True


def record_confirmed(conn: psycopg.Connection, cmd: Row, *, gateway_ref: str, now: datetime):
    """The gateway took the money: the TMF676 payment and the TMF654 top-up, together."""
    account = parties.get_billing_account(conn, cmd["billing_account_id"], "update")
    method = methods.get(conn, cmd["payment_method_id"])
    assert account is not None and method is not None  # foreign keys
    label = f"{method['brand'] or 'Card'} card" + (
        f" ending {method['last_four']}" if method["last_four"] else ""
    )
    payment, _ = post_payment(
        conn,
        account,
        PaymentCreate.model_validate(
            {
                "account": {"id": str(account["id"])},
                "totalAmount": {"unit": cmd["currency"], "value": cmd["amount"]},
                "paymentMethod": {"name": label, "@type": "PaymentMethodRef"},
            }
        ),
        {},
        now=now,
        operator=OPERATOR,
        record_topup=False,
    )
    bucket = balance_actions.account_bucket(conn, account["id"])
    if bucket is None:
        raise ValueError(f"account {account['id']} has no prepaid bucket")
    request = {
        "@type": "TopupBalance",
        "amount": {"amount": cmd["amount"], "units": cmd["currency"]},
        "usageType": "monetary",
        "bucket": {"id": str(bucket["id"])},
        "partyAccount": {"id": str(account["id"])},
        "isAutoTopup": cmd["purpose"] == "auto_topup",
        "paymentMethod": {
            "id": str(method["id"]),
            "href": method_href(method["id"]),
            "name": label,
            "@type": "PaymentMethodRef",
            "@referredType": "tokenizedCard",
        },
    }
    topup = balance_actions.insert_topup(
        conn,
        bucket,
        request=request,
        amount=cmd["amount"],
        operator=OPERATOR,
        now=now,
        reason=REASON[cmd["purpose"]],
        description=f"Gateway charge {gateway_ref} ({label})",
        payment_id=payment["id"],
    )
    emit.created(conn, TOPUP_API, TOPUP_RESOURCE, lambda: topup_body(conn, topup), at=now)
    return commands.confirm(
        conn,
        cmd["id"],
        gateway_ref=gateway_ref,
        payment_id=payment["id"],
        topup_id=topup["id"],
        now=now,
    )


def start_auto_topups(conn: psycopg.Connection, *, now: datetime) -> list[UUID]:
    """One charge of the set amount from the default card for each prepaid account whose
    spendable balance is below its threshold, with none in flight."""
    started = []
    for candidate in commands.auto_candidates(conn):
        with conn.transaction():
            parties.get_billing_account(conn, candidate["id"], "update")
            if funding.load(conn, candidate["id"]).spendable >= candidate["auto_topup_threshold"]:
                continue
            method = methods.default_of(conn, candidate["id"])
            if usable(method, now) is not None:
                continue
            try:
                with conn.transaction():
                    row = commands.insert(
                        conn,
                        account_id=candidate["id"],
                        method_id=method["id"],
                        purpose="auto_topup",
                        amount=candidate["auto_topup_amount"],
                        currency=candidate["currency"],
                        client_key=None,
                        request=charge_request(
                            method,
                            candidate["auto_topup_amount"],
                            candidate["currency"],
                            "auto_topup",
                        ),
                        now=now,
                    )
            except pg_errors.UniqueViolation:
                continue  # one is already in flight
            started.append(row["id"])
    return started
