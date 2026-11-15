"""What party interactions (TMF683) and trouble tickets (TMF621) share: the customer they
belong to, the channel, the things they are about, append-only notes and the entry each
piece of care work leaves in the account audit log. docs/decisions/care.md.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from pydantic import AwareDatetime, Field

from app.api.account import account_href
from app.api.common import Ref, TmfModel, ref, same, utc
from app.api.customer import customer_href
from app.api.errors import unprocessable
from app.api.inventory import product_href
from app.events.types import BASES
from app.repository import care as repo
from app.repository import security
from app.repository._sql import Row, fetch_one
from app.security import guard

CUSTOMER_ROLE = "customer"
AUDIT_BASE = "/internal/v1/security/audit"


def interaction_href(uid: Any) -> str:
    return f"{BASES['TMF683']}/partyInteraction/{uid}"


def ticket_href(uid: Any) -> str:
    return f"{BASES['TMF621']}/troubleTicket/{uid}"


def audit_href(uid: Any) -> str:
    return f"{AUDIT_BASE}?id={uid}"


HREFS = {
    "Product": product_href,
    "BillingAccount": account_href,
    "TroubleTicket": ticket_href,
    "PartyInteraction": interaction_href,
    "AuditEntry": audit_href,
}


class CustomerRoleRef(Ref):
    """The customer, as the specs' PartyRoleRef."""

    referred_type = "Customer"

    party_id: str | None = None
    party_name: str | None = None


class RelatedPartyIn(TmfModel):
    role: str = Field(min_length=1)
    party_or_party_role: CustomerRoleRef


class ChannelRefIn(TmfModel):
    """One of our channels, named by its id (`phone`, `chat`, `email`, `store`, `other`)."""

    id: str
    href: str | None = None
    name: str | None = None
    at_referred_type: str | None = Field(default=None, alias="@referredType")


class NoteIn(TmfModel):
    id: str | None = None
    author: str | None = None
    date: AwareDatetime | None = None
    text: str = Field(min_length=1)


class EntityRefIn(TmfModel):
    """A reference to what the care work is about: `@referredType` says which resource."""

    id: UUID
    href: str | None = None
    name: str | None = None
    at_referred_type: str = Field(alias="@referredType")


class RelatedEntityIn(TmfModel):
    role: str = Field(min_length=1)
    entity: EntityRefIn


def one_of(values: tuple[str, ...]):
    """A list filter converter for an attribute with fixed values (400 for another)."""

    def convert(raw: str) -> str:
        if raw not in values:
            raise ValueError(raw)
        return raw

    return convert


def choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise unprocessable(f"{name} must be one of {', '.join(allowed)}")
    return value


def channel_of(name: str, channel: ChannelRefIn) -> str:
    return choice(f"{name}.id", channel.id, repo.CHANNELS)


def refuse_unsupported(data: TmfModel, names: tuple[str, ...]) -> None:
    """Attributes the spec defines and this service does not build (422, never dropped)."""
    for name in names:
        if getattr(data, name) is not None:
            alias = type(data).model_fields[name].alias or name
            raise unprocessable(f"{alias} is not supported")


def customer_of(conn: psycopg.Connection, related: list[RelatedPartyIn] | None) -> Row:
    """The one customer a care resource belongs to."""
    if not related or len(related) != 1 or related[0].role != CUSTOMER_ROLE:
        raise unprocessable(
            f"relatedParty must name exactly one party, the customer (role {CUSTOMER_ROLE})"
        )
    uid = related[0].party_or_party_role.id
    row = fetch_one(conn, "SELECT * FROM customer WHERE id = %s FOR KEY SHARE", (uid,))
    if row is None:
        raise unprocessable(f"customer {uid} does not exist")
    return row


def _owner(conn: psycopg.Connection, kind: str, uid: UUID) -> tuple[UUID | None, UUID | None]:
    """(customer, billing account) of a referred resource; raises LookupError if absent.
    An audit entry may name no customer."""
    if kind == "Product":
        row = security.customer_of_product(conn, uid)
        if row is not None:
            return row["customer_id"], row["billing_account_id"]
    elif kind == "BillingAccount":
        customer = security.customer_of_account(conn, uid)
        if customer is not None:
            return customer, uid
    elif kind == "AuditEntry":
        row = repo.audit_entry(conn, uid)
        if row is not None:
            return row["customer_id"], None
    else:
        other = repo.TICKET if kind == "TroubleTicket" else repo.INTERACTION
        row = repo.get(conn, other, uid)
        if row is not None:
            return row["customer_id"], None
    raise LookupError(kind)


def link(
    conn: psycopg.Connection,
    kind: repo.Kind,
    customer_id: UUID,
    path: str,
    related: RelatedEntityIn,
    **item: Any,
) -> repo.Link:
    """One checked link: the entity must be of a type this resource links, exist, and belong
    to the same customer (422 otherwise)."""
    entity = related.entity
    referred = entity.at_referred_type
    if referred not in kind.link_types:
        raise unprocessable(
            f"{path}.entity.@referredType must be one of {', '.join(kind.link_types)}"
        )
    try:
        owner, account_id = _owner(conn, referred, entity.id)
    except LookupError:
        raise unprocessable(f"{path}.entity: {referred} {entity.id} does not exist") from None
    if owner is not None and owner != customer_id:
        raise unprocessable(f"{path}.entity: {referred} {entity.id} is of another customer")
    return repo.Link(referred, entity.id, related.role, account_id, **item)


def entity_body(row: Row) -> dict[str, Any]:
    kind = row["entity_type"]
    uid = row[repo.LINK_COLUMNS[kind]]
    return ref(kind, HREFS[kind](uid), uid, ref_type="EntityRef")


def customer_party(customer: Row, ref_type: str) -> dict[str, Any]:
    return {
        "role": CUSTOMER_ROLE,
        "partyOrPartyRole": ref(
            "Customer",
            customer_href(customer["id"]),
            customer["id"],
            customer["name"],
            ref_type="PartyRoleRef",
        ),
        "@type": ref_type,
    }


def channel_body(channel: str) -> dict[str, Any]:
    return {"id": channel, "name": channel, "@type": "ChannelRef"}


def note_body(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "author": row["author"],
        "date": utc(row["at"]),
        "text": row["text"],
        "@type": "Note",
    }


def new_notes(notes: list[NoteIn] | None, existing: list[Row]) -> list[str]:
    """The texts to append. Notes are append-only: an entry naming an existing note (a JSON
    Patch sends the whole array) must repeat it unchanged; any other entry is a new note,
    whose id, author and date the server sets (422 if sent)."""
    known = {str(row["id"]): note_body(row) for row in existing}
    texts = []
    for index, note in enumerate(notes or []):
        if note.id is not None and note.id in known:
            sent = note.model_dump(by_alias=True, exclude_none=True, mode="json")
            sent.pop("@type", None)
            if not same(sent, known[note.id]):
                raise unprocessable(f"note.{index}: a note cannot change (notes are append-only)")
            continue
        for name in ("id", "author", "date"):
            if getattr(note, name) is not None:
                raise unprocessable(f"note.{index}.{name} is set by the server")
        texts.append(note.text)
    return texts


def audit(
    conn: psycopg.Connection,
    actor: guard.Actor,
    now: datetime,
    action: str,
    customer_id: UUID,
    links: list[Row] | list[repo.Link],
    detail: dict[str, Any],
    reason: str | None = None,
) -> None:
    """The audit entry of one piece of care work (docs/decisions/care.md): it names the
    product and the billing account when the resource is about exactly one."""
    products, accounts = set(), set()
    for item in links:
        if isinstance(item, repo.Link):
            kind, uid, account = item.entity_type, item.entity_id, item.account_id
        else:
            kind, uid, account = (
                item["entity_type"],
                item[repo.LINK_COLUMNS[item["entity_type"]]],
                None,
            )
        if kind == "Product":
            products.add(uid)
            account = account or _owner(conn, kind, uid)[1]
        if kind in ("Product", "BillingAccount") and account is not None:
            accounts.add(account if kind == "Product" else uid)
    guard.done(
        conn,
        actor,
        now,
        action=action,
        verification="none",
        customer_id=customer_id,
        product_id=next(iter(products)) if len(products) == 1 else None,
        account_id=next(iter(accounts)) if len(accounts) == 1 else None,
        reason=reason,
        detail=detail,
    )
