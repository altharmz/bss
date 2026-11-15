"""Customer care: party interactions (TMF683) and trouble tickets (TMF621).
docs/decisions/care.md.

Both resources have the same shape: a row, links to what it is about (a product, a billing
account, the other care resource, an audit entry) and append-only notes; a ticket also keeps
every status it had. `Kind` names the tables of one resource so the functions serve both.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.repository._sql import (
    Row,
    conditions,
    fetch_all,
    fetch_one,
    returning,
    rows_grouped_by,
    select_by_id,
    update_by_id,
)

CHANNELS = ("phone", "chat", "email", "store", "other")
DIRECTIONS = ("inbound", "outbound")
INTERACTION_STATUSES = ("open", "inProgress", "completed")
SEVERITIES = ("minor", "major", "critical")
TICKET_TYPES = ("incident", "complaint", "request")
PRIORITIES = ("low", "medium", "high", "critical")
# TMF621 TroubleTicketStatusType, in the spec's order (the enum lock)
TICKET_STATUSES = (
    "acknowledged",
    "rejected",
    "pending",
    "held",
    "inProgress",
    "cancelled",
    "closed",
    "resolved",
)

# @referredType of a link -> its column
LINK_COLUMNS = {
    "Product": "product_id",
    "BillingAccount": "billing_account_id",
    "TroubleTicket": "trouble_ticket_id",
    "PartyInteraction": "party_interaction_id",
    "AuditEntry": "audit_id",
}


@dataclass(frozen=True)
class Kind:
    table: str
    links: str  # the table of what the resource is about
    notes: str
    parent: str  # the column of `links` and `notes` that names the resource
    link_types: tuple[str, ...]


INTERACTION = Kind(
    "party_interaction",
    "party_interaction_item",
    "party_interaction_note",
    "interaction_id",
    ("Product", "BillingAccount", "TroubleTicket", "AuditEntry"),
)
TICKET = Kind(
    "trouble_ticket",
    "trouble_ticket_related_entity",
    "trouble_ticket_note",
    "ticket_id",
    ("Product", "BillingAccount", "PartyInteraction", "AuditEntry"),
)


@dataclass(frozen=True)
class Link:
    """One thing a care resource is about, already checked to exist."""

    entity_type: str
    entity_id: UUID
    role: str
    account_id: UUID | None = None  # of a product or a billing account
    item_id: str | None = None  # interaction items only
    reason: str | None = None
    resolution: str | None = None
    item_type: str | None = None


def get(conn: psycopg.Connection, kind: Kind, uid: UUID, *, lock: bool = False) -> Row | None:
    return select_by_id(conn, kind.table, uid, "update" if lock else None)


def page(
    conn: psycopg.Connection, kind: Kind, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], bool]:
    """Newest first. `product_id` and `billing_account_id` match through the links."""
    where = dict(where)
    linked = {
        name: where.pop(name) for name in ("product_id", "billing_account_id") if name in where
    }
    filters = conditions(conn, kind.table, where)
    if filters is None:
        return [], False
    conds, params = filters
    for column, value in linked.items():
        conds.append(
            sql.SQL("EXISTS (SELECT 1 FROM {} l WHERE l.{} = {}.id AND l.{} = %s)").format(
                sql.Identifier(kind.links),
                sql.Identifier(kind.parent),
                sql.Identifier(kind.table),
                sql.Identifier(column),
            )
        )
        params.append(value)
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds) if conds else sql.SQL("")
    rows = fetch_all(
        conn,
        sql.SQL("SELECT * FROM {}").format(sql.Identifier(kind.table))
        + clause
        + sql.SQL(" ORDER BY created_at DESC, id DESC OFFSET %s LIMIT %s"),
        [*params, offset, limit + 1],
    )
    return rows[:limit], len(rows) > limit


def links_of(conn: psycopg.Connection, kind: Kind, ids: list[UUID]) -> dict[UUID, list[Row]]:
    return rows_grouped_by(conn, kind.links, kind.parent, ids, "position")


def notes_of(conn: psycopg.Connection, kind: Kind, ids: list[UUID]) -> dict[UUID, list[Row]]:
    return rows_grouped_by(conn, kind.notes, kind.parent, ids, "seq")


def history_of(conn: psycopg.Connection, ids: list[UUID]) -> dict[UUID, list[Row]]:
    return rows_grouped_by(conn, "trouble_ticket_status_change", "ticket_id", ids, "seq")


def add_notes(
    conn: psycopg.Connection, kind: Kind, uid: UUID, author: str, now: datetime, texts: list[str]
) -> list[Row]:
    query = sql.SQL(
        "INSERT INTO {} (id, {}, author, at, text) VALUES (%s, %s, %s, %s, %s) RETURNING *"
    ).format(sql.Identifier(kind.notes), sql.Identifier(kind.parent))
    return [returning(conn, query, (uuid4(), uid, author, now, text)) for text in texts]


def insert_interaction_items(conn: psycopg.Connection, uid: UUID, links: list[Link]) -> None:
    for position, link in enumerate(links):
        column = LINK_COLUMNS[link.entity_type]
        conn.execute(
            sql.SQL(
                "INSERT INTO party_interaction_item (id, interaction_id, position, item_id,"
                " reason, resolution, item_type, role, entity_type, {})"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(column)),
            (
                uuid4(),
                uid,
                position,
                link.item_id or str(position + 1),
                link.reason,
                link.resolution,
                link.item_type,
                link.role,
                link.entity_type,
                link.entity_id,
            ),
        )


def replace_ticket_entities(conn: psycopg.Connection, uid: UUID, links: list[Link]) -> None:
    conn.execute("DELETE FROM trouble_ticket_related_entity WHERE ticket_id = %s", (uid,))
    for position, link in enumerate(links):
        column = LINK_COLUMNS[link.entity_type]
        conn.execute(
            sql.SQL(
                "INSERT INTO trouble_ticket_related_entity (id, ticket_id, position, role,"
                " entity_type, {}) VALUES (%s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(column)),
            (uuid4(), uid, position, link.role, link.entity_type, link.entity_id),
        )


def insert_interaction(
    conn: psycopg.Connection,
    *,
    uid: UUID | None,
    customer_id: UUID,
    direction: str,
    reason: str,
    description: str | None,
    channel: str,
    channel_role: str,
    status: str,
    start: datetime | None,
    end: datetime | None,
    operator: str,
    now: datetime,
) -> Row:
    return returning(
        conn,
        "INSERT INTO party_interaction (id, customer_id, direction, reason, description, channel,"
        " channel_role, status, status_change_date, interaction_start, interaction_end,"
        " created_by, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            uid or uuid4(),
            customer_id,
            direction,
            reason,
            description,
            channel,
            channel_role,
            status,
            now,
            start,
            end,
            operator,
            now,
            now,
        ),
    )


def insert_ticket(
    conn: psycopg.Connection,
    *,
    uid: UUID | None,
    customer_id: UUID,
    name: str | None,
    description: str,
    severity: str,
    ticket_type: str,
    priority: str,
    channel: str | None,
    requested: datetime | None,
    expected: datetime | None,
    reason: str | None,
    operator: str,
    now: datetime,
) -> Row:
    row = returning(
        conn,
        "INSERT INTO trouble_ticket (id, customer_id, name, description, severity, ticket_type,"
        " priority, channel, requested_resolution_date, expected_resolution_date, status,"
        " status_change_date, status_change_reason, created_by, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'acknowledged', %s, %s, %s, %s, %s)"
        " RETURNING *",
        (
            uid or uuid4(),
            customer_id,
            name,
            description,
            severity,
            ticket_type,
            priority,
            channel,
            requested,
            expected,
            now,
            reason,
            operator,
            now,
            now,
        ),
    )
    add_status_change(conn, row["id"], "acknowledged", now, reason, operator)
    return row


def add_status_change(
    conn: psycopg.Connection,
    uid: UUID,
    status: str,
    now: datetime,
    reason: str | None,
    operator: str,
) -> None:
    conn.execute(
        "INSERT INTO trouble_ticket_status_change (id, ticket_id, status, at, reason, operator)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (uuid4(), uid, status, now, reason, operator),
    )


def update(conn: psycopg.Connection, kind: Kind, uid: UUID, changes: dict[str, Any]) -> Row:
    return update_by_id(conn, kind.table, uid, changes)


def audit_entry(conn: psycopg.Connection, uid: UUID) -> Row | None:
    return fetch_one(conn, "SELECT id, customer_id FROM account_audit WHERE id = %s", (uid,))
