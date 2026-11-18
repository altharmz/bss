"""Bill PDFs: what goes into one, and the stored documents (docs/decisions/bills.md, "PDF
documents and email delivery").

`store` renders the PDF of a bill the run has just issued and settled, in that transaction,
and keeps it in `customer_bill_document` (immutable: the PDF says what the bill said when it
was issued). Bills issued before migration 0086 have none.
"""

import base64
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing import bill_pdf
from app.config import settings
from app.repository import billing, presentation
from app.repository._sql import Row, fetch_all, fetch_one

ADDRESS_FIELDS = ("street1", "street2")


def _address(medium: dict) -> list[str]:
    city = ", ".join(
        part
        for part in (
            medium.get("city"),
            " ".join(p for p in (medium.get("stateOrProvince"), medium.get("postCode")) if p),
        )
        if part
    )
    return [
        line.strip()
        for line in (*(medium.get(f) for f in ADDRESS_FIELDS), city, medium.get("country"))
        if isinstance(line, str) and line.strip()
    ]


def billing_address(contacts: Any) -> list[str]:
    """The account's billing address lines: the first postal medium of a contact of type
    billingAddress, else of any contact (the service address). None found: no lines."""
    found: list[tuple[int, list[str]]] = []
    for contact in contacts if isinstance(contacts, list) else []:
        if not isinstance(contact, dict):
            continue
        rank = 0 if str(contact.get("contactType", "")).lower() == "billingaddress" else 1
        for medium in contact.get("contactMedium") or []:
            if isinstance(medium, dict) and medium.get("@type") == "GeographicAddressContactMedium":
                lines = _address(medium)
                if lines:
                    found.append((rank, lines))
                    break
    return min(found, key=lambda item: item[0])[1] if found else []


def _issuer_address() -> list[str]:
    raw = settings.bill_issuer_address.replace(";", "\n")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _applied(conn: psycopg.Connection, bill_id: UUID, tz: ZoneInfo) -> list[bill_pdf.Applied]:
    applied = []
    for source in billing.bill_sources(conn, bill_id):
        if source["payment_id"] is not None:
            when = source["received_at"].astimezone(tz).date()
            label = f"Payment received {bill_pdf.day(when)}"
        else:
            label = (
                f"Credit note {bill_pdf.bill_no(source['credit_bill_id'])}"
                f" of {bill_pdf.day(source['credit_bill_date'])}"
            )
        applied.append(bill_pdf.Applied(label, source["amount"]))
    return applied


def load(conn: psycopg.Connection, bill_id: UUID) -> bill_pdf.BillPdf:
    """Everything the bill's PDF prints, as stored now. The format is the one the bill's
    JSON snapshot was made with (it is written first, in the same transaction)."""
    bill = billing.get_bill(conn, bill_id)
    if bill is None:
        raise bill_pdf.RenderError(f"bill {bill_id} does not exist")
    account = fetch_one(
        conn,
        "SELECT a.id, a.name, a.details, c.name AS customer_name FROM billing_account a"
        " JOIN customer c ON c.id = a.customer_id WHERE a.id = %s",
        (bill["billing_account_id"],),
    )
    assert account is not None
    fmt = bill_pdf.DETAILED
    if bill["bill_document"] is not None:
        snapshot = json.loads(base64.b64decode(bill["bill_document"]))
        fmt = snapshot.get("format", {}).get("name", bill_pdf.DETAILED)
    currency = bill["currency"]
    net, tax_lines = billing.bill_taxes(conn, bill_id)
    charges = []
    if fmt == bill_pdf.DETAILED:
        charges = [
            bill_pdf.Charge(
                name=presentation._charge_name(a),
                start=a["period"].lower,
                end=a["period"].upper,
                amount=a["amount"],
                total=billing.abr_total(a),
            )
            for a in billing.bill_charges(conn, bill_id)
        ]
    return bill_pdf.BillPdf(
        bill_id=bill["id"],
        issuer_name=settings.bill_issuer_name,
        issuer_address=_issuer_address(),
        bill_date=bill["bill_date"],
        period_start=bill["bill_period"].lower,
        period_end=bill["bill_period"].upper,
        payment_due_date=bill["payment_due_date"],
        currency=currency,
        account_name=account["name"],
        account_id=account["id"],
        customer_name=account["customer_name"],
        billing_address=billing_address((account["details"] or {}).get("contact")),
        format_name=fmt,
        net=net,
        amount_due=bill["amount_due"],
        remaining=billing.remaining_amount(bill, billing.bill_allocated(conn, bill_id)),
        taxes=[bill_pdf.TaxLine(t["category"], t["rate"], t["amount"]) for t in tax_lines],
        charges=charges,
        applied=_applied(conn, bill_id, ZoneInfo(settings.billing_tz)),
    )


def store(conn: psycopg.Connection, bill_id: UUID, *, now: datetime) -> None:
    """Render and keep the bill's PDF, unless it has one. A RenderError propagates: the
    caller fails the account, so the bill is not issued without its document."""
    if fetch_one(
        conn, "SELECT 1 FROM customer_bill_document WHERE customer_bill_id = %s", (bill_id,)
    ):
        return
    pdf = bill_pdf.render(load(conn, bill_id))
    conn.execute(
        "INSERT INTO customer_bill_document (customer_bill_id, pdf, sha256, rendered_at)"
        " VALUES (%s, %s, %s, %s)",
        (bill_id, pdf, hashlib.sha256(pdf).hexdigest(), now),
    )


def pdf(conn: psycopg.Connection, bill_id: UUID) -> bytes | None:
    row = fetch_one(
        conn, "SELECT pdf FROM customer_bill_document WHERE customer_bill_id = %s", (bill_id,)
    )
    return None if row is None else bytes(row["pdf"])


def sizes(conn: psycopg.Connection, bill_ids: list[UUID]) -> dict[UUID, int]:
    """The PDF size in bytes of each bill that has one (a page of bills, one statement)."""
    if not bill_ids:
        return {}
    rows: list[Row] = fetch_all(
        conn,
        "SELECT customer_bill_id, octet_length(pdf) AS size FROM customer_bill_document"
        " WHERE customer_bill_id = ANY(%s)",
        (list(bill_ids),),
    )
    return {row["customer_bill_id"]: row["size"] for row in rows}
