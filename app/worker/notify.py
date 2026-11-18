"""Queue and send customer messages (docs/decisions/notifications.md).

Each tick: the detectors (app/notify/events.py) find the events customers are told about and
`notify` queues one message per event and channel (a repeated tick queues nothing twice);
then the due messages are sent through the adapter (app/integrations/notify). A marketing
message without opt-in is cancelled, an SMS inside the quiet hours waits for their end, a
failed send is retried with backoff and recorded `failed` after `notify_max_attempts`. A
detector that raises is logged and the others still run: billing never waits for a message.
"""

import logging
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import bill_pdf
from app.clock import Clock
from app.config import settings
from app.integrations.notify import NotifyError, adapter
from app.integrations.notify.interface import Attachment
from app.notify import events, service
from app.repository import bill_documents
from app.repository import notifications as repo
from app.repository._sql import Row
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")


def process_notifications(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
    accounts: list[UUID] | None = None,
) -> list[UUID]:
    """One pass; returns the ids of the messages queued or sent. `accounts` limits the
    per-account detector (low balance, grace) to those accounts; None: all."""
    ensure_ownership(conn)
    now = clock.now()
    acted: list[UUID] = []
    for name, run in events.detectors(now, tz, accounts):
        if checkpoint is not None and checkpoint():
            return acted
        try:
            found = run(conn)
        except Exception:  # noqa: BLE001 - a broken detector must not stop billing
            log.exception("notification detector %s", name)
            continue
        # the detectors find an event on every tick until it is too old: the ones told
        # already are dropped in one statement, not looked up one by one (#281)
        told = repo.existing_events(conn, [event.key for event in found])
        for event in found:
            if event.key in told:
                continue
            try:
                with conn.transaction():
                    acted += [row["id"] for row in service.notify(conn, event, now=now, tz=tz)]
            except Exception:  # noqa: BLE001 - one bad event must not stop the others
                log.exception("notification for %s", event.key)
    for message in repo.due(conn, now):
        if checkpoint is not None and checkpoint():
            return acted
        with conn.transaction():
            row = repo.get(conn, message["id"], lock=True)
            if row is None or row["state"] not in repo.OPEN or row["next_attempt_at"] > now:
                continue
            send(conn, row, now, tz)
        acted.append(message["id"])
    return acted


def send(conn: psycopg.Connection, row: Row, now: datetime, tz: ZoneInfo) -> Row:
    """Send one open message, unless consent or the quiet hours say not now."""
    if not service.consent_allows(conn, row):
        return repo.cancel(conn, row["id"], reason="noConsent")
    if row["channel"] == "sms" and not row["event_type"].startswith("security_"):
        until = service.quiet_until(now, tz)  # a security alert is not held for the quiet hours
        if until is not None:
            return repo.defer(conn, row["id"], until=until)
    try:
        if row["channel"] == "sms":
            ref = adapter().send_sms(str(row["id"]), row["receiver_phone"], row["content"])
        else:
            ref = adapter().send_email(
                str(row["id"]),
                row["receiver_email"],
                row["subject"] or "",
                row["content"],
                _attachments(conn, row),
            )
    except NotifyError as error:
        return _failed(conn, row, str(error) or "the sender refused the message", now)
    except Exception as error:  # noqa: BLE001 - unknown outcome: send again later, same key
        log.exception("notification %s", row["id"])
        return _failed(conn, row, str(error) or type(error).__name__, now)
    return repo.completed(conn, row["id"], ref=ref, now=now)


def _attachments(conn: psycopg.Connection, row: Row) -> tuple[Attachment, ...]:
    """The bill's PDF of a bill email (docs/decisions/bills.md); nothing for other messages.
    A bill email whose PDF is missing is not sent without it: the send fails and retries."""
    bill_id = row.get("customer_bill_id")
    if bill_id is None:
        return ()
    content = bill_documents.pdf(conn, bill_id)
    if content is None:
        raise NotifyError(f"bill {bill_id} has no PDF")
    return ((f"{bill_pdf.bill_no(bill_id)}.pdf", "application/pdf", content),)


def _failed(conn: psycopg.Connection, row: Row, error: str, now: datetime) -> Row:
    return repo.failed_attempt(
        conn, row["id"], error=error, max_attempts=settings.notify_max_attempts, now=now
    )
