"""TMF621 v5 Trouble Ticket: customer cases. docs/decisions/care.md.

A trouble ticket is a simple case with a status: what the customer reported, how severe it
is, what it is about (`relatedEntity`: a product, a billing account, a party interaction or
an audit log entry) and the agents' notes. GET list and retrieve, POST, PATCH and `/hub`. A
ticket is created `acknowledged` and moves only along the transitions below (409 otherwise);
`closed`, `rejected` and `cancelled` are final. DELETE is 405 (a case is a record), notes
are append-only, the status history is the server's. `troubleTicketSpecification` is not
implemented; `attachment`, `externalIdentifier`, `troubleTicketRelationship` and
`troubleTicketCharacteristic` are not supported (422). Opening a case, every status change
and every note added leave an entry in the account audit log.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request
from pydantic import AwareDatetime, Field

from app.api import care, listing
from app.api.common import (
    TmfModel,
    check_server_set,
    check_type,
    claim_id,
    created,
    meta_body,
    meta_columns,
    not_null,
    parse,
    parse_id,
    take_id,
    take_server_set,
    utc,
    with_meta,
    without_nulls,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.guards import actor_of
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.events.types import BASES
from app.repository import care as repo
from app.repository._sql import Row, rows_by_id

API = "TMF621"
BASE = BASES[API]
RESOURCE = "troubleTicket"
KIND = repo.TICKET
router = APIRouter(prefix=BASE)

ATTRIBUTES = (
    "name",
    "description",
    "severity",
    "ticketType",
    "priority",
    "channel",
    "creationDate",
    "lastUpdate",
    "requestedResolutionDate",
    "expectedResolutionDate",
    "resolutionDate",
    "status",
    "statusChangeDate",
    "statusChangeReason",
    "statusChangeHistory",
    "relatedParty",
    "relatedEntity",
    "note",
)
UNSUPPORTED = (
    "attachment",
    "external_identifier",
    "trouble_ticket_relationship",
    "trouble_ticket_specification",
    "trouble_ticket_characteristic",
)
# status -> the statuses it can move to; a status without an entry is final
TRANSITIONS = {
    "acknowledged": ("inProgress", "rejected", "cancelled"),
    "inProgress": ("pending", "held", "resolved", "cancelled"),
    "pending": ("inProgress", "cancelled"),
    "held": ("inProgress", "cancelled"),
    "resolved": ("closed", "inProgress"),
}
REASON_NEEDED = ("rejected", "cancelled", "pending", "held")
# attributes that only follow the status: alone they make no AttributeValueChange event
DERIVED = frozenset(
    {"statusChangeDate", "statusChangeReason", "statusChangeHistory", "resolutionDate"}
)
# column -> the request attribute that sets it
COLUMNS = {
    "name": "name",
    "description": "description",
    "severity": "severity",
    "ticket_type": "ticket_type",
    "priority": "priority",
    "requested_resolution_date": "requested_resolution_date",
    "expected_resolution_date": "expected_resolution_date",
}


class TicketCreate(TmfModel):
    description: str = Field(min_length=1)
    severity: str
    ticket_type: str
    name: str | None = Field(default=None, min_length=1)
    priority: str = "medium"
    channel: care.ChannelRefIn | None = None
    requested_resolution_date: AwareDatetime | None = None
    expected_resolution_date: AwareDatetime | None = None
    status_change_reason: str | None = Field(default=None, min_length=1)
    related_party: list[care.RelatedPartyIn] | None = None
    related_entity: list[care.RelatedEntityIn] | None = None
    note: list[care.NoteIn] | None = None
    attachment: Any = None
    external_identifier: Any = None
    trouble_ticket_relationship: Any = None
    trouble_ticket_specification: Any = None
    trouble_ticket_characteristic: Any = None


class TicketPatch(TmfModel):
    description: str | None = Field(default=None, min_length=1)
    severity: str | None = None
    ticket_type: str | None = None
    name: str | None = Field(default=None, min_length=1)
    priority: str | None = None
    channel: care.ChannelRefIn | None = None
    requested_resolution_date: AwareDatetime | None = None
    expected_resolution_date: AwareDatetime | None = None
    status: str | None = None
    status_change_reason: str | None = Field(default=None, min_length=1)
    related_entity: list[care.RelatedEntityIn] | None = None
    note: list[care.NoteIn] | None = None
    attachment: Any = None
    external_identifier: Any = None
    trouble_ticket_relationship: Any = None
    trouble_ticket_specification: Any = None
    trouble_ticket_characteristic: Any = None


def _date(value: Any) -> Any:
    return None if value is None else utc(value)


def _body(
    row: Row, customer: Row, entities: list[Row], notes: list[Row], history: list[Row]
) -> dict[str, Any]:
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": care.ticket_href(row["id"]),
            "@type": "TroubleTicket",
            "name": row["name"],
            "description": row["description"],
            "severity": row["severity"],
            "ticketType": row["ticket_type"],
            "priority": row["priority"],
            "channel": None if row["channel"] is None else care.channel_body(row["channel"]),
            "creationDate": utc(row["created_at"]),
            "lastUpdate": utc(row["updated_at"]),
            "requestedResolutionDate": _date(row["requested_resolution_date"]),
            "expectedResolutionDate": _date(row["expected_resolution_date"]),
            "resolutionDate": _date(row["resolution_date"]),
            "status": row["status"],
            "statusChangeDate": utc(row["status_change_date"]),
            "statusChangeReason": row["status_change_reason"],
            "statusChangeHistory": [
                without_nulls(
                    {
                        "status": change["status"],
                        "statusChangeDate": utc(change["at"]),
                        "statusChangeReason": change["reason"],
                        "@type": "StatusChange",
                    }
                )
                for change in history
            ],
            "relatedParty": [care.customer_party(customer, "RelatedPartyRefOrPartyRoleRef")],
            "relatedEntity": [
                {
                    "role": entity["role"],
                    "entity": care.entity_body(entity),
                    "@type": "RelatedEntity",
                }
                for entity in entities
            ],
            "note": [care.note_body(note) for note in notes],
        }
    ) | meta_body(row)


def ticket_bodies(conn: psycopg.Connection, rows: list[Row]) -> list[dict[str, Any]]:
    """The bodies of a page: customers, entities, notes and the status history are read for
    the whole page at once."""
    ids = [row["id"] for row in rows]
    customers = rows_by_id(conn, "customer", [row["customer_id"] for row in rows])
    entities = repo.links_of(conn, KIND, ids)
    notes = repo.notes_of(conn, KIND, ids)
    history = repo.history_of(conn, ids)
    return [
        _body(
            row,
            customers[row["customer_id"]],
            entities[row["id"]],
            notes[row["id"]],
            history[row["id"]],
        )
        for row in rows
    ]


def ticket_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return ticket_bodies(conn, [row])[0]


FILTERS = {
    "id": ("id", listing.as_uuid),
    "status": ("status", care.one_of(repo.TICKET_STATUSES)),
    "severity": ("severity", care.one_of(repo.SEVERITIES)),
    "ticketType": ("ticket_type", care.one_of(repo.TICKET_TYPES)),
    "priority": ("priority", care.one_of(repo.PRIORITIES)),
    "relatedParty.id": ("customer_id", listing.as_uuid),
    "product.id": ("product_id", listing.as_uuid),
    "billingAccount.id": ("billing_account_id", listing.as_uuid),
}


def _choices(data: TicketCreate | TicketPatch) -> None:
    for name, value, allowed in (
        ("severity", data.severity, repo.SEVERITIES),
        ("ticketType", data.ticket_type, repo.TICKET_TYPES),
        ("priority", data.priority, repo.PRIORITIES),
    ):
        if value is not None:
            care.choice(name, value, allowed)


def _links(
    conn: psycopg.Connection, customer_id: Any, related: list[care.RelatedEntityIn] | None
) -> list[repo.Link]:
    return [
        care.link(conn, KIND, customer_id, f"relatedEntity.{index}", entity)
        for index, entity in enumerate(related or [])
    ]


@router.get(f"/{RESOURCE}")
def list_trouble_tickets(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=FILTERS, attributes=ATTRIBUTES)
    with tx() as conn:
        rows, more = repo.page(conn, KIND, query.where, query.offset, query.limit)
        bodies = ticket_bodies(conn, rows)
    return listing.list_response(bodies, more, query)


@router.get(f"/{RESOURCE}/{{ticket_id}}")
def get_trouble_ticket(ticket_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, KIND, parse_id(RESOURCE, ticket_id))
        if row is None:
            raise not_found(RESOURCE, ticket_id)
        return TmfJSONResponse(listing.select_fields(ticket_body(conn, row), fields))


@router.post(f"/{RESOURCE}")
def create_trouble_ticket(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """A ticket is created `acknowledged`."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "TroubleTicket")
    uid = take_id(body)
    server_set = take_server_set(body, ("href", "status", "statusChangeDate", "resolutionDate"))
    data = parse(TicketCreate, body)
    care.refuse_unsupported(data, UNSUPPORTED)
    _choices(data)
    channel = None if data.channel is None else care.channel_of("channel", data.channel)
    texts = care.new_notes(data.note, [])
    actor = actor_of(request)
    now = clock.now()
    with tx() as conn:
        claim_id(conn, KIND.table, uid)
        customer = care.customer_of(conn, data.related_party)
        links = _links(conn, customer["id"], data.related_entity)
        row = repo.insert_ticket(
            conn,
            uid=uid,
            customer_id=customer["id"],
            name=data.name,
            description=data.description,
            severity=data.severity,
            ticket_type=data.ticket_type,
            priority=data.priority,
            channel=channel,
            requested=data.requested_resolution_date,
            expected=data.expected_resolution_date,
            reason=data.status_change_reason,
            operator=actor.operator,
            now=now,
        )
        repo.replace_ticket_entities(conn, row["id"], links)
        repo.add_notes(conn, KIND, row["id"], actor.operator, now, texts)
        row = with_meta(conn, KIND.table, row, data)
        care.audit(
            conn,
            actor,
            now,
            "ticket_opened",
            customer["id"],
            links,
            {
                "ticket": str(row["id"]),
                "status": row["status"],
                "severity": row["severity"],
                "ticketType": row["ticket_type"],
                "notes": len(texts),
            },
        )
        new = ticket_body(conn, row)
        check_server_set(server_set, new)
        emit.created(conn, API, RESOURCE, new, at=now)
    return created(listing.select_fields(new, fields))


def _status_change(row: Row, data: TicketPatch, now: Any) -> dict[str, Any]:
    """The columns a status change sets (none when the status stays); 409 for a move the
    transitions do not allow, 422 for a missing reason."""
    if data.status is None or data.status == row["status"]:
        if data.status_change_reason is not None:
            raise unprocessable("statusChangeReason is given with a status change only")
        return {}
    care.choice("status", data.status, repo.TICKET_STATUSES)
    allowed = TRANSITIONS.get(row["status"], ())
    if data.status not in allowed:
        raise conflict(
            f"a ticket cannot move from {row['status']} to {data.status}"
            f" (allowed: {', '.join(allowed) or 'none'})"
        )
    reopened = row["status"] == "resolved" and data.status == "inProgress"
    if (data.status in REASON_NEEDED or reopened) and data.status_change_reason is None:
        raise unprocessable(
            f"statusChangeReason is required to move from {row['status']} to {data.status}"
        )
    changes = {
        "status": data.status,
        "status_change_date": now,
        "status_change_reason": data.status_change_reason,
    }
    if data.status == "resolved":
        changes["resolution_date"] = now
    elif reopened:
        changes["resolution_date"] = None
    return changes


@router.patch(f"/{RESOURCE}/{{ticket_id}}")
def patch_trouble_ticket(
    ticket_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Changes the case, moves its status and adds notes; a final ticket takes no change."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "TroubleTicket")
    server_set = take_server_set(body, ("id", "href", "resolutionDate", "statusChangeHistory"))
    data = parse(TicketPatch, body, read_only=("relatedParty",))
    care.refuse_unsupported(data, UNSUPPORTED)
    not_null(
        data,
        "description",
        "severity",
        "ticket_type",
        "priority",
        "status",
        "note",
        "related_entity",
    )
    _choices(data)
    channel = None if data.channel is None else care.channel_of("channel", data.channel)
    actor = actor_of(request)
    now = clock.now()
    with tx() as conn:
        uid = parse_id(RESOURCE, ticket_id)
        row = repo.get(conn, KIND, uid, lock=True)
        if row is None:
            raise not_found(RESOURCE, ticket_id)
        before = ticket_body(conn, row)
        check_server_set(server_set, before)
        if row["status"] not in TRANSITIONS and data.model_fields_set - {"at_type"}:
            raise conflict(f"the ticket is {row['status']}: a final ticket takes no change")
        old = emit.before(conn, API, RESOURCE, lambda: before, kind="StatusChange")
        changes: dict[str, Any] = {
            column: getattr(data, name)
            for column, name in COLUMNS.items()
            if name in data.model_fields_set and getattr(data, name) != row[column]
        }
        if "channel" in data.model_fields_set and channel != row["channel"]:
            changes["channel"] = channel
        changes |= meta_columns(data)
        moved = _status_change(row, data, now)
        changes |= moved
        relinked = data.related_entity is not None
        if relinked:
            repo.replace_ticket_entities(
                conn, uid, _links(conn, row["customer_id"], data.related_entity)
            )
        texts = care.new_notes(data.note, repo.notes_of(conn, KIND, [uid])[uid])
        added = repo.add_notes(conn, KIND, uid, actor.operator, now, texts)
        if changes or added or relinked:
            row = repo.update(conn, KIND, uid, changes | {"updated_at": now})
        entities = repo.links_of(conn, KIND, [uid])[uid]
        if moved:
            repo.add_status_change(
                conn, uid, row["status"], now, row["status_change_reason"], actor.operator
            )
            care.audit(
                conn,
                actor,
                now,
                "ticket_status_changed",
                row["customer_id"],
                entities,
                {"ticket": str(uid), "from": before["status"], "to": row["status"]},
                reason=row["status_change_reason"],
            )
        for note in added:
            care.audit(
                conn,
                actor,
                now,
                "care_note_added",
                row["customer_id"],
                entities,
                {"ticket": str(uid), "note": str(note["id"])},
            )
        new = ticket_body(conn, row)
        emit.changed(
            conn,
            API,
            RESOURCE,
            old,
            new,
            at=now,
            state="status",
            kind="StatusChange",
            derived=DERIVED,
        )
        if moved and row["status"] == "resolved":
            emit.emit(conn, API, RESOURCE, "Resolved", new, at=now)
    return TmfJSONResponse(listing.select_fields(new, fields))
