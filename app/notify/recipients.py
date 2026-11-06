"""Who a customer message goes to: the customer's contact media, else the line's number."""

import re
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from app.repository._sql import fetch_one

PHONE = re.compile(r"^[1-9][0-9]{7,14}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Contact:
    customer_id: UUID
    name: str
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)


def normalize_phone(raw: str) -> str | None:
    """E.164 digits without the plus; a 10-digit US number gets the country code."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "1" + digits
    return digits if PHONE.match(digits) else None


def _media(details: dict) -> tuple[list[str], list[str]]:
    emails: list[tuple[bool, str]] = []
    phones: list[tuple[bool, str]] = []
    for medium in details.get("contactMedium") or []:
        preferred = bool(medium.get("preferred"))
        email = medium.get("emailAddress")
        if isinstance(email, str) and EMAIL.match(email.strip()):
            emails.append((not preferred, email.strip()))
        phone = medium.get("phoneNumber")
        number = normalize_phone(phone) if isinstance(phone, str) else None
        if number is not None:
            phones.append((not preferred, number))
    return [e for _, e in sorted(emails)], [p for _, p in sorted(phones)]


def _contact(row: dict | None) -> Contact | None:
    if row is None:
        return None
    emails: list[str] = []
    phones: list[str] = []
    for details in (row["customer_details"], row["party_details"]):  # customer first
        found_emails, found_phones = _media(details or {})
        emails += [e for e in found_emails if e not in emails]
        phones += [p for p in found_phones if p not in phones]
    name = row["given_name"] or row["organization_name"] or row["name"]
    return Contact(row["customer_id"], name, emails, phones)


_SELECT = (
    "SELECT c.id AS customer_id, c.name, c.details AS customer_details,"
    " p.details AS party_details, p.given_name, p.organization_name FROM customer c"
    " JOIN party p ON p.id = c.party_id"
)


def for_account(conn: psycopg.Connection, account_id: UUID) -> Contact | None:
    return _contact(
        fetch_one(
            conn,
            _SELECT + " JOIN billing_account a ON a.customer_id = c.id WHERE a.id = %s",
            (account_id,),
        )
    )


def for_customer(conn: psycopg.Connection, customer_id: UUID) -> Contact | None:
    return _contact(fetch_one(conn, _SELECT + " WHERE c.id = %s", (customer_id,)))


def line_number(conn: psycopg.Connection, product_id: UUID) -> str | None:
    """The line's current (else latest) MSISDN."""
    row = fetch_one(
        conn,
        "SELECT n.value FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE a.product_id = %s"
        " AND n.resource_specification_id = 'msisdn' AND a.cancelled_at IS NULL"
        " ORDER BY a.effective_from DESC LIMIT 1",
        (product_id,),
    )
    return None if row is None else row["value"]


def account_line_number(conn: psycopg.Connection, account_id: UUID) -> str | None:
    """The number of the account's first open line, for an event of the whole account."""
    row = fetch_one(
        conn,
        "SELECT n.value FROM product p JOIN number_assignment a ON a.product_id = p.id"
        " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
        " WHERE p.billing_account_id = %s AND n.resource_specification_id = 'msisdn'"
        " AND a.cancelled_at IS NULL AND upper_inf(a.valid_for)"
        " AND p.status NOT IN ('terminated', 'cancelled', 'aborted')"
        " ORDER BY p.created_at, a.effective_from DESC LIMIT 1",
        (account_id,),
    )
    return None if row is None else row["value"]
