"""The connector's own state: SQLite on the mcp_data volume - never the bss database.
Losing the file only means clients re-authorize and static tokens are issued again.

Tokens are stored as SHA-256 hashes only; a token is shown once, when it is created.
"""

import hashlib
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from typing import Literal

from bss_mcp.clock import Clock

Scope = Literal["read", "write"]
SCOPES: tuple[Scope, ...] = ("read", "write")

# The principal is "mcp:<name>" and goes to the app as X-Remote-User, whose operator
# pattern is ^[A-Za-z0-9._@:-]{1,64}$ (app/api/common.py): keep names well inside it.
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
STATIC_PREFIX = "bss_st_"

SCHEMA = """
CREATE TABLE IF NOT EXISTS static_token (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    scope       TEXT NOT NULL CHECK (scope IN ('read', 'write')),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    revoked_at  REAL
);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class StaticToken:
    id: str
    name: str
    scope: Scope
    created_at: float
    revoked_at: float | None


class Store:
    def __init__(self, path: str, clock: Clock) -> None:
        self.clock = clock
        # one connection, serialised: the service is one process and the store is tiny
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def ping(self) -> None:
        with self._lock:
            self._db.execute("SELECT 1").fetchone()

    # --- static tokens ---

    def create_static_token(self, name: str, scope: Scope) -> tuple[StaticToken, str]:
        """The new token's record and the token itself - the only time it exists in clear."""
        if not NAME.match(name):
            raise ValueError(f"token name must match {NAME.pattern}")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        token = STATIC_PREFIX + secrets.token_urlsafe(32)
        record = StaticToken(
            id=secrets.token_hex(8),
            name=name,
            scope=scope,
            created_at=self.clock.now(),
            revoked_at=None,
        )
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO static_token (id, name, scope, token_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (record.id, name, scope, token_hash(token), record.created_at),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"a token named {name} already exists") from None
        return record, token

    def static_tokens(self) -> list[StaticToken]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, scope, created_at, revoked_at FROM static_token ORDER BY name"
            ).fetchall()
        return [StaticToken(**dict(row)) for row in rows]

    def revoke_static_token(self, token_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE static_token SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (self.clock.now(), token_id),
            )
        return cursor.rowcount == 1

    def find_static_token(self, token: str) -> StaticToken | None:
        """The live token with this value, or None (unknown or revoked)."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, name, scope, created_at, revoked_at FROM static_token"
                " WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash(token),),
            ).fetchone()
        return StaticToken(**dict(row)) if row else None
