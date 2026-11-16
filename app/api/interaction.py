"""TMF683 v5 Party Interaction: the notes on a subscriber. docs/decisions/care.md.

A party interaction is one recorded contact with a customer (a call, a chat, an email, a
store visit): who, through which channel, why, what it was about (`interactionItem`: a
product, a billing account, a trouble ticket or an audit log entry) and the agent's notes.
GET list and retrieve, POST, PATCH and `/hub`. An interaction is a record: DELETE is 405, its
notes are append-only, and once `completed` it only takes more notes. `attachment`,
`interactionRelationship` and `externalIdentifier` are not supported (422). Every interaction
logged and every note added leaves an entry in the account audit log.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field

from app.api import care, listing
from app.api.common import (
    TimePeriod,
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

API = "TMF683"
BASE = BASES[API]
RESOURCE = "partyInteraction"
KIND = repo.INTERACTION
router = APIRouter(prefix=BASE)

ATTRIBUTES = (
    "relatedChannel",
    "direction",
    "reason",
    "interactionDate",
    "description",
    "status",
    "statusChangeDate",
    "creationDate",
    "lastUpdate",
    "relatedParty",
    "note",
    "interactionItem",
)
UNSUPPORTED = ("attachment", "interaction_relationship", "external_identifier")
# what a PATCH cannot change: an interaction is a record of a contact that happened
PATCH_READ_ONLY = ("relatedChannel", "relatedParty", "interactionItem", "interactionDate")
# attributes that only follow the status: alone they make no AttributeValueChange event
DERIVED = frozenset({"statusChangeDate"})


class RelatedChannelIn(TmfModel):
    role: str = Field(min_length=1)
    channel: care.ChannelRefIn


class InteractionItemIn(TmfModel):
    id: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    resolution: str | None = None
    interaction_item_type: str | None = None
    item: care.RelatedEntityIn


class InteractionCreate(TmfModel):
    direction: str
    reason: str = Field(min_length=1)
    related_channel: list[RelatedChannelIn] = Field(min_length=1, max_length=1)
    related_party: list[care.RelatedPartyIn] | None = None
    description: str | None = None
    status: str = "completed"
    interaction_date: TimePeriod | None = None
    interaction_item: list[InteractionItemIn] | None = None
    note: list[care.NoteIn] | None = None
    attachment: Any = None
    interaction_relationship: Any = None
    external_identifier: Any = None


class InteractionPatch(TmfModel):
    reason: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: str | None = None
    note: list[care.NoteIn] | None = None
    attachment: Any = None
    interaction_relationship: Any = None
    external_identifier: Any = None


def _item_body(row: Row) -> dict[str, Any]:
    return without_nulls(
        {
            "id": row["item_id"],
            "reason": row["reason"],
            "resolution": row["resolution"],
            "interactionItemType": row["item_type"],
            "item": {
                "role": row["role"],
                "entity": care.entity_body(row),
                "@type": "RelatedEntityRefOrValue",
            },
            "@type": "InteractionItem",
        }
    )


def _body(row: Row, customer: Row, items: list[Row], notes: list[Row]) -> dict[str, Any]:
    period = None
    if row["interaction_start"] is not None:
        period = {"startDateTime": utc(row["interaction_start"])}
        if row["interaction_end"] is not None:
            period["endDateTime"] = utc(row["interaction_end"])
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": care.interaction_href(row["id"]),
            "@type": "PartyInteraction",
            "direction": row["direction"],
            "reason": row["reason"],
            "description": row["description"],
            "status": row["status"],
            "statusChangeDate": utc(row["status_change_date"]),
            "creationDate": utc(row["created_at"]),
            "lastUpdate": utc(row["updated_at"]),
            "interactionDate": period,
            "relatedChannel": [
                {
                    "role": row["channel_role"],
                    "channel": care.channel_body(row["channel"]),
                    "@type": "RelatedChannel",
                }
            ],
            "relatedParty": [care.customer_party(customer, "RelatedPartyOrPartyRole")],
            "interactionItem": [_item_body(item) for item in items],
            "note": [care.note_body(note) for note in notes],
        }
    ) | meta_body(row)


def interaction_bodies(conn: psycopg.Connection, rows: list[Row]) -> list[dict[str, Any]]:
    """The bodies of a page: customers, items and notes are read for the whole page at once."""
    ids = [row["id"] for row in rows]
    customers = rows_by_id(conn, "customer", [row["customer_id"] for row in rows])
    items = repo.links_of(conn, KIND, ids)
    notes = repo.notes_of(conn, KIND, ids)
    return [
        _body(row, customers[row["customer_id"]], items[row["id"]], notes[row["id"]])
        for row in rows
    ]


def interaction_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return interaction_bodies(conn, [row])[0]


FILTERS = {
    "id": ("id", listing.as_uuid),
    "status": ("status", care.one_of(repo.INTERACTION_STATUSES)),
    "direction": ("direction", care.one_of(repo.DIRECTIONS)),
    "relatedParty.id": ("customer_id", listing.as_uuid),
    "product.id": ("product_id", listing.as_uuid),
    "billingAccount.id": ("billing_account_id", listing.as_uuid),
}


@router.get(f"/{RESOURCE}")
def list_party_interactions(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(request, filters=FILTERS, attributes=ATTRIBUTES)
    with tx() as conn:
        rows, more = repo.page(conn, KIND, query.where, query.offset, query.limit)
        bodies = interaction_bodies(conn, rows)
    return listing.list_response(bodies, more, query)


@router.get(f"/{RESOURCE}/{{interaction_id}}")
def get_party_interaction(interaction_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.get(conn, KIND, parse_id(RESOURCE, interaction_id))
        if row is None:
            raise not_found(RESOURCE, interaction_id)
        return TmfJSONResponse(listing.select_fields(interaction_body(conn, row), fields))


@router.post(f"/{RESOURCE}")
def create_party_interaction(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "PartyInteraction")
    uid = take_id(body)
    server_set = take_server_set(body, ("lastUpdate",))
    data = parse(InteractionCreate, body)
    care.refuse_unsupported(data, UNSUPPORTED)
    care.choice("direction", data.direction, repo.DIRECTIONS)
    care.choice("status", data.status, repo.INTERACTION_STATUSES)
    related = data.related_channel[0]
    channel = care.channel_of("relatedChannel.0.channel", related.channel)
    period = (
        None if data.interaction_date is None else data.interaction_date.to_range("interactionDate")
    )
    texts = care.new_notes(data.note, [])
    actor = actor_of(request)
    now = clock.now()
    with tx() as conn:
        claim_id(conn, KIND.table, uid)
        customer = care.customer_of(conn, data.related_party)
        links = []
        seen: set[str] = set()
        for index, item in enumerate(data.interaction_item or []):
            item_id = item.id or str(index + 1)
            if item_id in seen:
                raise unprocessable(f"interactionItem.{index}.id {item_id} is given twice")
            seen.add(item_id)
            links.append(
                care.link(
                    conn,
                    KIND,
                    customer["id"],
                    f"interactionItem.{index}.item",
                    item.item,
                    item_id=item_id,
                    reason=item.reason,
                    resolution=item.resolution,
                    item_type=item.interaction_item_type,
                )
            )
        row = repo.insert_interaction(
            conn,
            uid=uid,
            customer_id=customer["id"],
            direction=data.direction,
            reason=data.reason,
            description=data.description,
            channel=channel,
            channel_role=related.role,
            status=data.status,
            start=None if period is None else period.lower,
            end=None if period is None else period.upper,
            operator=actor.operator,
            now=now,
        )
        repo.insert_interaction_items(conn, row["id"], links)
        repo.add_notes(conn, KIND, row["id"], actor.operator, now, texts)
        row = with_meta(conn, KIND.table, row, data)
        care.audit(
            conn,
            actor,
            now,
            "interaction_logged",
            customer["id"],
            links,
            {
                "interaction": str(row["id"]),
                "status": row["status"],
                "direction": row["direction"],
                "channel": row["channel"],
                "notes": len(texts),
            },
        )
        new = interaction_body(conn, row)
        check_server_set(server_set, new)
        emit.created(conn, API, RESOURCE, new, at=now)
    return created(listing.select_fields(new, fields))


@router.patch(f"/{RESOURCE}/{{interaction_id}}")
def patch_party_interaction(
    interaction_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Adds notes; changes `status`, `description` and `reason` until it is completed."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "PartyInteraction")
    server_set = take_server_set(body, ("lastUpdate",))
    data = parse(InteractionPatch, body, read_only=PATCH_READ_ONLY)
    care.refuse_unsupported(data, UNSUPPORTED)
    not_null(data, "reason", "status", "note")
    if data.status is not None:
        care.choice("status", data.status, repo.INTERACTION_STATUSES)
    actor = actor_of(request)
    now = clock.now()
    with tx() as conn:
        uid = parse_id(RESOURCE, interaction_id)
        row = repo.get(conn, KIND, uid, lock=True)
        if row is None:
            raise not_found(RESOURCE, interaction_id)
        before = interaction_body(conn, row)
        check_server_set(server_set, before)
        old = emit.before(conn, API, RESOURCE, lambda: before, kind="StatusChange")
        changes: dict[str, Any] = {
            name: getattr(data, name)
            for name in ("reason", "description")
            if name in data.model_fields_set and getattr(data, name) != row[name]
        }
        if data.status is not None and data.status != row["status"]:
            changes |= {"status": data.status, "status_change_date": now}
        changes |= meta_columns(data)
        if changes and row["status"] == "completed":
            raise conflict("the interaction is completed: it only takes more notes")
        texts = care.new_notes(data.note, repo.notes_of(conn, KIND, [uid])[uid])
        added = repo.add_notes(conn, KIND, uid, actor.operator, now, texts)
        if changes or added:
            row = repo.update(conn, KIND, uid, changes | {"updated_at": now})
        items = repo.links_of(conn, KIND, [uid])[uid]
        for note in added:
            care.audit(
                conn,
                actor,
                now,
                "care_note_added",
                row["customer_id"],
                items,
                {"interaction": str(uid), "note": str(note["id"])},
            )
        new = interaction_body(conn, row)
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
    return TmfJSONResponse(listing.select_fields(new, fields))
