"""Queue customer messages for events and decide whether they may go (consent, quiet hours).

`notify` makes one message per channel the customer can be reached on, once per event
(`Event.key` is the dedupe key). A marketing message without a recorded opt-in is stored
`cancelled` (`noConsent`) and never reaches the sender. An SMS created inside the quiet hours
is scheduled for their end. docs/decisions/notifications.md.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.config import settings
from app.notify import recipients, templates
from app.repository import notifications as repo
from app.repository._sql import Row


@dataclass(frozen=True)
class Event:
    type: str
    key: str
    account_id: UUID | None
    product_id: UUID | None
    params: dict[str, Any] = field(default_factory=dict)
    sms_to_line: bool = True  # False: the line is gone, SMS only to a phone contact
    # Who to tell, when it is not the account's customer as it is now (a contact change tells
    # the addresses on record before the change).
    contact: recipients.Contact | None = None
    urgent: bool = False  # a security alert: an SMS does not wait for the end of quiet hours
    channels: tuple[str, ...] = ("sms", "email")  # a bill goes by email only
    customer_bill_id: UUID | None = None  # the bill whose PDF the email carries


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def quiet_until(now: datetime, tz: ZoneInfo) -> datetime | None:
    """The end of the SMS quiet hours when `now` is inside them, else None. The window may
    cross midnight (21:00 to 08:00); start == end means no quiet hours."""
    start, end = _clock(settings.notify_quiet_start), _clock(settings.notify_quiet_end)
    if start == end:
        return None
    local = now.astimezone(tz)
    at = local.time()
    inside = (start <= at < end) if start < end else (at >= start or at < end)
    if not inside:
        return None
    day = local.date()
    if start > end and at >= start:
        day += timedelta(days=1)
    return datetime.combine(day, end, tz).astimezone(now.tzinfo)


def consent_allows(conn: psycopg.Connection, row: Row) -> bool:
    if row["category"] != "marketing":
        return True
    return row["customer_id"] is not None and repo.opted_in(
        conn, row["customer_id"], row["channel"]
    )


def notify(conn: psycopg.Connection, event: Event, *, now: datetime, tz: ZoneInfo) -> list[Row]:
    """The messages made for an event (none when it was already notified)."""
    contact = event.contact
    if contact is None and event.account_id:
        contact = recipients.for_account(conn, event.account_id)
    if contact is None or repo.event_exists(conn, event.key):
        return []  # an event is told once, on whichever channels could be reached then
    phones = list(contact.phones) if "sms" in event.channels else []
    if not phones and event.sms_to_line and "sms" in event.channels:
        number = (
            recipients.line_number(conn, event.product_id)
            if event.product_id is not None
            else recipients.account_line_number(conn, event.account_id)
            if event.account_id is not None
            else None
        )
        phones = [number] if number else []
    params = {"name": contact.name, **event.params}
    made: list[Row] = []
    targets: list[tuple[str, str | None, str | None]] = []
    if phones:
        targets.append(("sms", phones[0], None))
    if contact.emails and "email" in event.channels:
        targets.append(("email", None, contact.emails[0]))
    if not targets:
        # one cancelled row says nobody could be reached
        targets = [(event.channels[0], None, None)]
    for channel, phone, email in targets:
        subject, body, template = templates.render(event.type, channel, params)
        reachable = phone is not None or email is not None
        row = repo.insert(
            conn,
            event_type=event.type,
            category="transactional",
            channel=channel,
            template=template,
            state="initial" if reachable else "cancelled",
            state_reason=None if reachable else "noRecipient",
            receiver_name=contact.name,
            receiver_email=email,
            receiver_phone=phone,
            customer_id=contact.customer_id,
            billing_account_id=event.account_id,
            product_id=event.product_id,
            subject=subject,
            content=body,
            now=now,
            dedupe_key=event.key,
            scheduled_send_time=(
                quiet_until(now, tz) if channel == "sms" and not event.urgent else None
            ),
            customer_bill_id=event.customer_bill_id,
        )
        if row is not None:
            made.append(row)
    return made
