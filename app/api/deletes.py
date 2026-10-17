"""DELETE removes only what nothing references: a 409 names the references otherwise,
with nothing changed. Deletes lock their row FOR UPDATE; writers that reference it hold
it FOR SHARE (or FOR KEY SHARE), so the two never race."""

from uuid import UUID

import psycopg
from psycopg import errors as pg_errors

from app.api.errors import conflict
from app.repository._sql import delete_by_id


def refuse_if_referenced(resource: str, uid: UUID, counts: dict[str, int], advice: str) -> None:
    if counts:
        listed = ", ".join(f"{n} {label}" for label, n in counts.items())
        raise conflict(f"{resource} {uid} is still referenced by {listed}: {advice}")


def delete_row(conn: psycopg.Connection, table: str, uid: UUID) -> None:
    """The last step of a delete; the foreign keys (all NO ACTION) back the checks."""
    try:
        with conn.transaction():
            delete_by_id(conn, table, uid)
    except pg_errors.ForeignKeyViolation:
        raise conflict(f"{uid} is still referenced") from None
