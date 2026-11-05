"""Taxes of prepaid charges from the tax engine (docs/decisions/taxes.md).

An account with a service address gets its prepaid charges taxed by the engine
(app/integrations/tax) for that address; one without keeps the flat tax of the price.
`quote_taxes` is read-only at the engine. `commit` runs when the charge is booked, in the
posting's transaction, and `void` when the whole charge is credited: an engine error
raises `TaxUnavailable` and the transaction rolls back, so nothing is booked or credited
without its tax and the command is retried.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from app.billing.core import charge_taxes
from app.integrations.tax import ChargeLine, TaxUnavailable, adapter
from app.repository._sql import fetch_one, jsonb

SERVICE_ADDRESS = "serviceAddress"
STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH"
    " NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI".split()
)
ZIP = re.compile(r"^\d{5}(-\d{4})?$")

__all__ = ["TaxUnavailable", "address_problems", "quote_taxes", "service_address_of"]


def address_problems(medium: dict) -> list[str]:
    """What is wrong with a US postal address in the TMF666 GeographicAddressContactMedium
    shape (street1, street2, city, stateOrProvince, postCode, country)."""
    found = []
    for name in ("street1", "city"):
        if not isinstance(medium.get(name), str) or not medium[name].strip():
            found.append(f"{name} is required")
    if medium.get("country") not in ("US", "USA"):
        found.append("country must be US")
    if medium.get("stateOrProvince") not in STATES:
        found.append("stateOrProvince must be a US state code")
    if not isinstance(medium.get("postCode"), str) or not ZIP.match(medium["postCode"]):
        found.append("postCode must be a ZIP (12345) or ZIP+4 (12345-6789)")
    return found


def service_address_of(contacts: list | None) -> tuple[dict | None, list[tuple[str, str]]]:
    """The normalized service address of an account's `contact` document (the contact of
    type `serviceAddress`, its first geographic address medium) and the problems found as
    (path, message). None when there is no such contact."""
    for i, contact in enumerate(contacts or []):
        if not isinstance(contact, dict) or contact.get("contactType") != SERVICE_ADDRESS:
            continue
        for j, medium in enumerate(contact.get("contactMedium") or []):
            if (
                not isinstance(medium, dict)
                or medium.get("@type") != "GeographicAddressContactMedium"
            ):
                continue
            path = f"contact.{i}.contactMedium.{j}"
            problems = [(path, message) for message in address_problems(medium)]
            if problems:
                return None, problems
            address = {
                "street1": medium["street1"].strip(),
                "city": medium["city"].strip(),
                "stateOrProvince": medium["stateOrProvince"],
                "postCode": medium["postCode"],
                "country": "US",
            }
            if medium.get("street2"):
                address["street2"] = medium["street2"].strip()
            return address, []
        return None, [
            (f"contact.{i}", "a serviceAddress contact needs a GeographicAddressContactMedium")
        ]
    return None, []


def address_of(conn, account_id: UUID) -> dict | None:
    row = fetch_one(
        conn, "SELECT service_address FROM billing_account WHERE id = %s", (account_id,)
    )
    return row["service_address"] if row else None


def quote_taxes(
    conn,
    account,
    account_id: UUID,
    price,
    net: Decimal,
    day: date,
    tz: ZoneInfo,
    *,
    transaction_type: str = "recurring",
):
    """(tax lines as TaxQuote, quote id) of a charge of `net` on `day`: from the engine for
    the account's service address, else the price's flat taxes and no quote id. Raises
    TaxUnavailable when the engine cannot answer."""
    from app.billing.prepaid import TaxQuote

    address = address_of(conn, account_id)
    if address is None:
        return [
            TaxQuote(category=t.category, rate=t.rate, amount=t.amount)
            for t in charge_taxes(net, price, account, day, tz)
        ], None
    result = adapter().quote(
        [ChargeLine(net, str(price.offering_id), transaction_type)], address, day
    )
    return [
        TaxQuote(
            category=line.tax_type,
            rate=line.rate,
            amount=line.amount,
            jurisdiction=line.jurisdiction,
        )
        for line in result.lines[0]
    ], result.quote_id


def key_of(applied_rate_id: UUID) -> str:
    return str(uuid5(NAMESPACE_URL, f"tax:{applied_rate_id}"))


def commit(conn, abr, account_id: UUID, quote, *, now: datetime) -> None:
    """Commit the quote of a booked charge at the engine and record the transaction."""
    address = address_of(conn, account_id)
    adapter().commit(key_of(abr["id"]), quote.tax_quote_id)
    conn.execute(
        "INSERT INTO tax_transaction (applied_rate_id, billing_account_id, quote_id, address,"
        " lines, committed_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            abr["id"],
            account_id,
            quote.tax_quote_id,
            jsonb(address or {}),
            jsonb(
                [
                    {
                        "jurisdiction": t.jurisdiction,
                        "taxCategory": t.category,
                        "taxRate": t.rate,
                        "taxAmount": t.amount,
                    }
                    for t in quote.taxes
                ]
            ),
            now,
        ),
    )


def void(conn, charge_id: UUID, *, now: datetime) -> bool:
    """Void the engine transaction of a charge that is credited in full. False when the
    charge has none (flat tax)."""
    row = fetch_one(
        conn,
        "SELECT * FROM tax_transaction WHERE applied_rate_id = %s AND state = 'committed'"
        " FOR UPDATE",
        (charge_id,),
    )
    if row is None:
        return False
    adapter().void(key_of(charge_id), row["quote_id"])
    conn.execute(
        "UPDATE tax_transaction SET state = 'voided', voided_at = %s WHERE id = %s",
        (now, row["id"]),
    )
    return True
