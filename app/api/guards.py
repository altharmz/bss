"""Account security at the API edge (docs/decisions/accounts.md): who is acting and which PIN
they sent, and the PIN check of a contact or address change."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import Request

from app.api.common import operator_of
from app.api.deps import Tx
from app.notify import recipients
from app.repository import security as repo
from app.repository._sql import fetch_one
from app.security import guard

__all__ = ["Ticket", "actor_of", "authorize_contact"]


@dataclass(frozen=True)
class Ticket:
    """A contact change that was allowed: what its audit entry needs once it is made."""

    actor: guard.Actor
    mode: str
    customer_id: UUID | None
    account_id: UUID | None
    key: str
    alert_to: dict[str, list[str]] = field(default_factory=dict)

    def record(self, conn: Any, now: Any) -> None:
        guard.done(
            conn,
            self.actor,
            now,
            action="contact_change",
            verification=self.mode,
            customer_id=self.customer_id,
            account_id=self.account_id,
            detail={"attribute": self.key, "alertTo": self.alert_to},
        )


def actor_of(request: Request) -> guard.Actor:
    """The authenticated operator and the X-Account-Pin they sent (never logged)."""
    pin = request.headers.get(guard.PIN_HEADER)
    return guard.Actor(
        operator_of(request) or "unknown",
        getattr(request.state, "role", None),
        pin.strip() if pin else None,
    )


CONTACT_KEY = {"party": "contactMedium", "customer": "contactMedium", "account": "contact"}
TABLE = {"party": "party", "customer": "customer", "account": "billing_account"}


def authorize_contact(
    request: Request, tx: Tx, now: Any, kind: str, raw_id: str, documents: dict[str, Any]
) -> Ticket | None:
    """Before a PATCH of a party, customer or billing account that may change its contact
    (`contactMedium`, `contact` of an account): when it does, for a protected customer (a live
    prepaid product), the PIN is checked in a transaction of its own, so the refusal's audit
    entry and the wrong-PIN count are kept when the request fails. Returns what the handler
    needs to write the audit entry once the change is made, or None (nothing to verify). A
    party with several protected customers is verified against any one of them."""
    key = CONTACT_KEY[kind]
    try:
        uid = UUID(raw_id)
    except ValueError:
        return None  # the handler refuses the id itself
    if key not in documents:
        return None
    actor = actor_of(request)
    account_id = None
    with tx() as conn:
        row = fetch_one(conn, f"SELECT * FROM {TABLE[kind]} WHERE id = %s", (uid,))  # noqa: S608
        if row is None or documents[key] == (row["details"] or {}).get(key):
            return None
        if kind == "party":
            candidates = repo.customers_of_party(conn, uid)
        elif kind == "account":
            account_id = uid
            candidates = [row["customer_id"]]
        else:
            candidates = [uid]
        protected = repo.protected(conn, candidates)
        if not protected:
            return None
        customer = protected[0] if len(protected) == 1 else None
        account = account_id or (None if customer is None else repo.first_account(conn, customer))
        try:
            mode = guard.gate(
                conn,
                actor,
                now,
                action="contact_change",
                customer_ids=protected,
                account_id=account,
                detail={"attribute": key},
            )
            refusal = None
        except guard.Refusal as exc:
            refusal, mode = exc, "none"
        before = recipients.for_customer(conn, protected[0])
    if refusal is not None:
        raise refusal.error()
    alert_to = {"phones": before.phones, "emails": before.emails} if before else {}
    return Ticket(actor, mode, customer, account, key, alert_to)
