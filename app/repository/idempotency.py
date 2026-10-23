"""Idempotency-Key journal: replay of a stored success response.

Call within the request's transaction, before any other lock. `begin` takes a
transaction-scoped advisory lock on the key, so concurrent duplicates queue behind the
first and then replay it. `store` writes the response in that same transaction: a
rollback leaves no key, and only a committed success is ever replayed.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.api import tmfjson
from app.api.errors import bad_request, conflict
from app.repository._sql import fetch_one, jsonb

KEY = re.compile(r"[\x21-\x7e]{1,255}")


@dataclass(frozen=True)
class Replay:
    status_code: int
    response: Any
    location: str | None


def check_key(key: str) -> str:
    if not KEY.fullmatch(key):
        raise bad_request("Idempotency-Key must be 1-255 visible ASCII characters")
    return key


def _normal(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normal(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_normal(v) for v in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def request_hash(query: list[tuple[str, str]], body: Any) -> str:
    """Key order and number spelling (10 vs 10.00) do not make a different request."""
    text = tmfjson.dumps({"query": sorted(query), "body": _normal(body)})
    return hashlib.sha256(text.encode()).hexdigest()


def begin(
    conn: psycopg.Connection, operator: str, method: str, route: str, key: str, digest: str
) -> Replay | None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"idempotency|{operator}|{method}|{route}|{key}",),
    )
    row = fetch_one(
        conn,
        "SELECT * FROM idempotency_key WHERE operator = %s AND method = %s AND route = %s"
        " AND key = %s",
        (operator, method, route, key),
    )
    if row is None:
        return None
    if row["request_hash"] != digest:
        raise conflict("Idempotency-Key was already used with a different request")
    if row["tombstoned_at"] is not None:
        raise conflict("Idempotency-Key belongs to a resource that was deleted")
    return Replay(row["status_code"], row["response"], row["location"])


def store(
    conn: psycopg.Connection,
    operator: str,
    method: str,
    route: str,
    key: str,
    digest: str,
    *,
    status_code: int,
    response: dict[str, Any],
    location: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code,"
        " response, location, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (operator, method, route, key, digest, status_code, jsonb(response), location, now),
    )


def tombstone(conn: psycopg.Connection, location: str, *, now: datetime) -> int:
    """Retire every key whose stored response created the resource at `location`."""
    return conn.execute(
        "UPDATE idempotency_key SET tombstoned_at = %s WHERE location = %s"
        " AND tombstoned_at IS NULL",
        (now, location),
    ).rowcount
