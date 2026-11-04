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

-- OAuth 2.1 (bss_mcp/oauth.py). Clients from dynamic registration, kept as the SDK's
-- OAuthClientInformationFull JSON.
CREATE TABLE IF NOT EXISTS oauth_client (
    client_id   TEXT PRIMARY KEY,
    info        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
-- An /authorize request waiting for the login page: the request id is the page's only
-- handle (unguessable, single use) and therefore also its CSRF token.
CREATE TABLE IF NOT EXISTS oauth_pending (
    id_hash     TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    params      TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_code (
    code_hash   TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    data        TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
-- Access and refresh tokens. A grant is one login; refreshing keeps the grant, and
-- revoking a refresh token revokes the whole grant.
CREATE TABLE IF NOT EXISTS oauth_token (
    token_hash  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    grant_id    TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    scopes      TEXT NOT NULL,
    resource    TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    revoked_at  REAL,
    -- the app user's credential_version this grant was issued for (docs/mcp.md)
    credential_version INTEGER
);
CREATE INDEX IF NOT EXISTS oauth_token_grant ON oauth_token (grant_id);
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
            self._migrate()

    def _migrate(self) -> None:
        """OAuth grants from before the app's users (no credential_version) are dropped:
        the clients authorize once more. Static tokens are kept."""
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(oauth_token)")}
        if "credential_version" not in columns:
            self._db.execute("ALTER TABLE oauth_token ADD COLUMN credential_version INTEGER")
        self._db.execute("DELETE FROM oauth_token WHERE credential_version IS NULL")
        self._db.execute("DELETE FROM oauth_code WHERE data NOT LIKE '%credential_version%'")

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

    # --- OAuth: clients ---

    def count_clients(self) -> int:
        with self._lock:
            return self._db.execute("SELECT count(*) FROM oauth_client").fetchone()[0]

    def save_client(self, client_id: str, info: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO oauth_client (client_id, info, created_at) VALUES (?, ?, ?)",
                (client_id, info, self.clock.now()),
            )

    def client(self, client_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT info FROM oauth_client WHERE client_id = ?", (client_id,)
            ).fetchone()
        return row["info"] if row else None

    # --- OAuth: authorization requests waiting for the login page ---

    def save_pending(self, request_id: str, client_id: str, params: str, ttl: float) -> None:
        with self._lock:
            self._db.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (self.clock.now(),))
            self._db.execute(
                "INSERT INTO oauth_pending (id_hash, client_id, params, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash(request_id), client_id, params, self.clock.now() + ttl),
            )

    def pending(self, request_id: str) -> sqlite3.Row | None:
        """The live request (client_id, params, attempts), or None."""
        with self._lock:
            return self._db.execute(
                "SELECT client_id, params, attempts FROM oauth_pending"
                " WHERE id_hash = ? AND expires_at >= ?",
                (token_hash(request_id), self.clock.now()),
            ).fetchone()

    def fail_pending(self, request_id: str, max_attempts: int) -> None:
        """One more failed login on this request; it is dropped at `max_attempts`."""
        with self._lock:
            self._db.execute(
                "UPDATE oauth_pending SET attempts = attempts + 1 WHERE id_hash = ?",
                (token_hash(request_id),),
            )
            self._db.execute(
                "DELETE FROM oauth_pending WHERE id_hash = ? AND attempts >= ?",
                (token_hash(request_id), max_attempts),
            )

    def drop_pending(self, request_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM oauth_pending WHERE id_hash = ?", (token_hash(request_id),)
            )
        return cursor.rowcount == 1

    # --- OAuth: authorization codes (single use) ---

    def save_code(self, code: str, client_id: str, data: str, ttl: float) -> None:
        with self._lock:
            self._db.execute("DELETE FROM oauth_code WHERE expires_at < ?", (self.clock.now(),))
            self._db.execute(
                "INSERT INTO oauth_code (code_hash, client_id, data, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash(code), client_id, data, self.clock.now() + ttl),
            )

    def code(self, code: str) -> sqlite3.Row | None:
        """The live code (client_id, data), or None (unknown, used or expired)."""
        with self._lock:
            return self._db.execute(
                "SELECT client_id, data FROM oauth_code WHERE code_hash = ? AND expires_at >= ?",
                (token_hash(code), self.clock.now()),
            ).fetchone()

    def use_code(self, code: str) -> bool:
        """Consume the code; False if it was already used (or never existed)."""
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM oauth_code WHERE code_hash = ?", (token_hash(code),)
            )
        return cursor.rowcount == 1

    # --- OAuth: access and refresh tokens ---

    def save_token(
        self,
        token: str,
        *,
        kind: str,
        grant_id: str,
        client_id: str,
        subject: str,
        scopes: list[str],
        resource: str | None,
        credential_version: int,
        ttl: float,
    ) -> float:
        """Stores the token's hash; returns its expiry (epoch seconds)."""
        now = self.clock.now()
        with self._lock:
            self._db.execute(
                "INSERT INTO oauth_token (token_hash, kind, grant_id, client_id, subject, scopes,"
                " resource, credential_version, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    token_hash(token),
                    kind,
                    grant_id,
                    client_id,
                    subject,
                    " ".join(scopes),
                    resource,
                    credential_version,
                    now,
                    now + ttl,
                ),
            )
        return now + ttl

    def token(self, token: str, kind: str) -> sqlite3.Row | None:
        """The live token of this kind (not revoked, not expired), or None."""
        with self._lock:
            return self._db.execute(
                "SELECT grant_id, client_id, subject, scopes, resource, credential_version,"
                " expires_at"
                " FROM oauth_token WHERE token_hash = ? AND kind = ? AND revoked_at IS NULL"
                " AND expires_at >= ?",
                (token_hash(token), kind, self.clock.now()),
            ).fetchone()

    def revoke_token(self, token: str) -> sqlite3.Row | None:
        """Revoke one token; its row (kind, grant_id) if it was live."""
        with self._lock:
            row = self._db.execute(
                "SELECT kind, grant_id FROM oauth_token"
                " WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash(token),),
            ).fetchone()
            self._db.execute(
                "UPDATE oauth_token SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (self.clock.now(), token_hash(token)),
            )
        return row

    def revoke_grant(self, grant_id: str) -> int:
        """Revoke every token of a grant (one login); how many were live."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE oauth_token SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL",
                (self.clock.now(), grant_id),
            )
        return cursor.rowcount

    def grants(self) -> list[sqlite3.Row]:
        """Live grants (a live refresh or access token), for the token tooling."""
        with self._lock:
            return self._db.execute(
                "SELECT grant_id, client_id, subject, scopes, min(created_at) AS created_at,"
                " max(expires_at) AS expires_at FROM oauth_token"
                " WHERE revoked_at IS NULL AND expires_at >= ?"
                " GROUP BY grant_id ORDER BY created_at",
                (self.clock.now(),),
            ).fetchall()
