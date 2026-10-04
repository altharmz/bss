"""The worker's database connection (W1) and the ownership lock that lives on it.

One dedicated, long-lived connection per worker process, autocommit, with explicit
transactions for each unit of work. The session advisory lock that says "this process
owns billing" is taken on it and all the work runs on it, so the lock and the work fail
together: a dead connection frees the lock and stops the work at the same moment. That
is what makes a takeover safe (docs/stage4-design.md, section 2; CLAUDE.md, "Decided
(stage 4 planning)").
"""

import psycopg

from app.config import settings

# The one advisory lock key of the billing worker ('bss' + 1). Advisory locks are scoped
# to a database, so every database (each test's clone included) has its own.
OWNERSHIP_KEY = 0x62737301

# W1: fail fast on a stalled connect, notice a half-open connection within ~60 s instead of
# hanging on it forever, and never wait more than 30 s for a row lock.
CONNECTION = {
    "connect_timeout": settings.db_connect_timeout,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "application_name": "bss-worker",
    "options": "-c lock_timeout=30s",
}


def connect(url: str | None = None) -> psycopg.Connection:
    """The worker's connection: autocommit, so nothing stays idle in a transaction."""
    return psycopg.connect(url or settings.database_url, autocommit=True, **CONNECTION)


def acquire_ownership(conn: psycopg.Connection) -> bool:
    """Try to own billing; False if another live session does. Never waits."""
    return conn.execute("SELECT pg_try_advisory_lock(%s)", (OWNERSHIP_KEY,)).fetchone()[0]


def holds_ownership(conn: psycopg.Connection) -> bool:
    """Whether this very session holds the ownership lock (a bigint key shows up in
    pg_locks as classid = high 32 bits, objid = low 32 bits, objsubid = 1)."""
    return conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
             WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
               AND classid = %s AND objid = %s AND objsubid = 1)
        """,
        (OWNERSHIP_KEY >> 32, OWNERSHIP_KEY & 0xFFFFFFFF),
    ).fetchone()[0]


def release_ownership(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (OWNERSHIP_KEY,))
