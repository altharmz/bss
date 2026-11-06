"""TMF681 v4 Communication Management: the log of customer messages (SMS and email).
docs/decisions/notifications.md.

GET list and retrieve; POST queues a message an operator writes (the worker sends it through
the notify adapter); PATCH can only cancel a message that has not been sent. The messages the
system itself writes for prepaid events (low balance, renewal, grace, ...) are listed here too.
A message is a record of what was sent: DELETE is 405, and the spec's `/hub` is not
implemented (it declares no listener events). A marketing message (characteristic
`category` = `marketing`) is cancelled with `noConsent` unless the customer opted in.
`attachment` and `logFlag: false` are not supported (422).
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request
from pydantic import AwareDatetime, Field

from app.api import listing
from app.api.common import (
    Ref,
    TmfModel,
    billing_tz,
    check_type,
    created,
    parse,
    parse_id,
    ref,
    utc,
    without_nulls,
)
from app.api.customer import customer_href
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.notify import recipients, service
from app.repository import notifications as repo
from app.repository._sql import Row

API = "TMF681"
BASE = "/tmf-api/communicationManagement/v4"
RESOURCE = "communicationMessage"
router = APIRouter(prefix=BASE)

TYPES = {"sms": "SMS", "email": "Email"}
CHANNELS = {"sms": "sms", "email": "email"}
ATTRIBUTES = (
    "content",
    "description",
    "messageType",
    "priority",
    "scheduledSendTime",
    "sendTime",
    "sendTimeComplete",
    "subject",
    "tryTimes",
    "characteristic",
    "receiver",
    "sender",
    "state",
)
CATEGORY_NAMES = ("category",)
READ_ONLY = ("sendTime", "sendTimeComplete", "tryTimes", "state")


class CustomerRef(Ref):
    referred_type = "Customer"


class ReceiverIn(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, min_length=3)
    phone_number: str | None = Field(default=None, min_length=1)
    party: CustomerRef | None = None


class SenderIn(TmfModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    party: Any = None


class CharacteristicIn(TmfModel):
    name: str = Field(min_length=1)
    value: Any
    value_type: str | None = None
    id: str | None = None


class MessageCreate(TmfModel):
    content: str = Field(min_length=1)
    message_type: str
    receiver: list[ReceiverIn] = Field(min_length=1, max_length=1)
    sender: SenderIn
    subject: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority: str | None = Field(default=None, min_length=1)
    scheduled_send_time: AwareDatetime | None = None
    characteristic: list[CharacteristicIn] | None = None
    attachment: Any = None
    log_flag: bool | None = None


class MessagePatch(TmfModel):
    state: str | None = None


def message_href(message_id: Any) -> str:
    return f"{BASE}/{RESOURCE}/{message_id}"


def _trait(name: str, value: Any) -> dict[str, Any]:
    return {"@type": "StringCharacteristic", "name": name, "valueType": "string", "value": value}


def message_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    traits = [_trait("category", row["category"]), _trait("event", row["event_type"])]
    traits.append(_trait("template", row["template"]))
    for name, value in (
        ("stateReason", row["state_reason"]),
        ("lastError", row["last_error"]),
        ("providerReference", row["provider_ref"]),
        ("resendOf", None if row["resend_of"] is None else str(row["resend_of"])),
        (
            "billingAccount",
            None if row["billing_account_id"] is None else str(row["billing_account_id"]),
        ),
        ("product", None if row["product_id"] is None else str(row["product_id"])),
    ):
        if value is not None:
            traits.append(_trait(name, value))
    party = None
    if row["customer_id"] is not None:
        party = ref(
            "Customer",
            customer_href(row["customer_id"]),
            row["customer_id"],
            row["receiver_name"],
            ref_type="RelatedParty",
        )
    sender = row["sender"] or {
        "@type": "Sender",
        "name": "bss",
        "email": settings.notify_sender_email,
        "phoneNumber": settings.notify_sender_sms,
    }
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": message_href(row["id"]),
            "@type": "CommunicationMessage",
            "content": row["content"],
            "description": row["description"],
            "messageType": TYPES[row["channel"]],
            "priority": row["priority"],
            "scheduledSendTime": None
            if row["scheduled_send_time"] is None
            else utc(row["scheduled_send_time"]),
            "sendTime": None if row["send_time"] is None else utc(row["send_time"]),
            "sendTimeComplete": None
            if row["send_time_complete"] is None
            else utc(row["send_time_complete"]),
            "subject": row["subject"],
            "tryTimes": row["try_times"],
            "characteristic": traits,
            "receiver": [
                without_nulls(
                    {
                        "@type": "Receiver",
                        "name": row["receiver_name"],
                        "email": row["receiver_email"],
                        "phoneNumber": row["receiver_phone"],
                        "party": party,
                    }
                )
            ],
            "sender": sender,
            "state": row["state"],
        }
    )


def _channel(raw: str) -> str:
    for channel, label in TYPES.items():
        if raw.lower() == label.lower():
            return channel
    raise ValueError(raw)


FILTERS = {
    "id": ("id", listing.as_uuid),
    "state": ("state", listing.as_str),
    "messageType": ("channel", lambda raw: _channel(raw)),
    "receiver.party.id": ("customer_id", listing.as_uuid),
    "billingAccount.id": ("billing_account_id", listing.as_uuid),
    "product.id": ("product_id", listing.as_uuid),
}


@router.get(f"/{RESOURCE}")
def list_communication_messages(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=FILTERS, attributes=ATTRIBUTES)
    with tx() as conn:
        rows, total = repo.page(conn, query.where, query.offset, query.limit)
        bodies = [message_body(conn, row) for row in rows]
    return listing.list_response(bodies, total, query)


@router.get(f"/{RESOURCE}/{{message_id}}")
def get_communication_message(message_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, parse_id(RESOURCE, message_id))
        if row is None:
            raise not_found(RESOURCE, message_id)
        return TmfJSONResponse(listing.select_fields(message_body(conn, row), fields))


def _category(data: MessageCreate) -> str:
    category = "transactional"
    seen: set[str] = set()
    for item in data.characteristic or []:
        if item.name not in CATEGORY_NAMES:
            raise unprocessable(
                f"characteristic {item.name} is not supported (only: {', '.join(CATEGORY_NAMES)})"
            )
        if item.name in seen:
            raise unprocessable(f"characteristic {item.name} is given twice")
        seen.add(item.name)
        if item.value not in repo.CATEGORIES:
            raise unprocessable(
                f"characteristic category must be one of {', '.join(repo.CATEGORIES)}"
            )
        category = item.value
    return category


def _address(conn: psycopg.Connection, data: MessageCreate, channel: str):
    """(customer id, name, email, phone) of the one receiver, completed from the customer."""
    receiver = data.receiver[0]
    contact = None
    if receiver.party is not None:
        contact = recipients.for_customer(conn, receiver.party.id)
        if contact is None:
            raise unprocessable(f"customer {receiver.party.id} does not exist")
    email = receiver.email
    phone = None
    if receiver.phone_number is not None:
        phone = recipients.normalize_phone(receiver.phone_number)
        if phone is None:
            raise unprocessable("receiver.phoneNumber is not a valid phone number")
    if channel == "email" and email is None and contact and contact.emails:
        email = contact.emails[0]
    if channel == "sms" and phone is None and contact and contact.phones:
        phone = contact.phones[0]
    if channel == "email" and email is None:
        raise unprocessable("an Email needs receiver.email (or a customer with an email contact)")
    if channel == "sms" and phone is None:
        raise unprocessable(
            "an SMS needs receiver.phoneNumber (or a customer with a phone contact)"
        )
    return (
        None if contact is None else contact.customer_id,
        receiver.name or (contact.name if contact else None),
        email if channel == "email" else None,
        phone if channel == "sms" else None,
    )


@router.post(f"/{RESOURCE}")
def create_communication_message(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "CommunicationMessage")
    data = parse(MessageCreate, body, read_only=READ_ONLY)
    if data.attachment is not None:
        raise unprocessable("attachment is not supported")
    if data.log_flag is False:
        raise unprocessable("logFlag false is not supported: every message is logged")
    try:
        channel = _channel(data.message_type)
    except ValueError:
        raise unprocessable("messageType must be SMS or Email") from None
    if channel == "email" and data.subject is None:
        raise unprocessable("an Email needs a subject")
    category = _category(data)
    now = clock.now()
    with tx() as conn:
        customer_id, name, email, phone = _address(conn, data, channel)
        quiet = service.quiet_until(now, billing_tz()) if channel == "sms" else None
        scheduled = max(filter(None, (data.scheduled_send_time, quiet)), default=None)
        consent = category != "marketing" or (
            customer_id is not None and repo.opted_in(conn, customer_id, channel)
        )
        row = repo.insert(
            conn,
            event_type="operator",
            category=category,
            channel=channel,
            template="operator",
            state="initial" if consent else "cancelled",
            state_reason=None if consent else "noConsent",
            receiver_name=name,
            receiver_email=email,
            receiver_phone=phone,
            customer_id=customer_id,
            billing_account_id=None,
            product_id=None,
            subject=data.subject,
            content=data.content,
            now=now,
            scheduled_send_time=scheduled,
            description=data.description,
            priority=data.priority,
            sender=data.sender.model_dump(by_alias=True, exclude_none=True, mode="json")
            | {"@type": "Sender"},
        )
        assert row is not None
        new = message_body(conn, row)
    return created(listing.select_fields(new, fields))


@router.patch(f"/{RESOURCE}/{{message_id}}")
def patch_communication_message(
    message_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    """The only change is `state: cancelled` of a message not sent yet (409 otherwise)."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "CommunicationMessage")
    data = parse(MessagePatch, body)
    if data.state != "cancelled":
        raise unprocessable("only state can change, and only to cancelled")
    with tx() as conn:
        uid = parse_id(RESOURCE, message_id)
        row = repo.get(conn, uid, lock=True)
        if row is None:
            raise not_found(RESOURCE, message_id)
        if row["state"] not in repo.OPEN:
            raise conflict(
                f"the message is {row['state']}: only an unsent message can be cancelled"
            )
        new = message_body(conn, repo.cancel(conn, uid, reason="operator"))
    return TmfJSONResponse(listing.select_fields(new, fields))
