"""Names of the throwaway databases the tests create, and the sweep of the ones a run
left behind.

A name carries its creation time: `vpslab_<kind>_<unix seconds>_<8 hex>`. A run that is
cut short (a killed hook, a stopped container) cannot drop its databases; the next test
session drops every `vpslab_` database older than STALE_AFTER, and every one without a
time (named before names carried one). Never one in use: DROP without FORCE fails on a
database that has a session, and that one is left for a later sweep.

One kind of database is kept on purpose: the migrated template the test databases are
cloned from, `vpslab_kept_template_<digest of the migrations>`. It is built once and
reused by every worker and every later run while the migrations are unchanged. The sweep
never touches a kept name; building the template for a new digest drops the older ones.
"""

import hashlib
import re
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

import psycopg
from psycopg import sql

PREFIX = "vpslab_"
# Far longer than any run (make progress included): a live run's databases are younger
STALE_AFTER = 6 * 3600
_TIMED = re.compile(r"vpslab_[a-z]+_(\d+)_[0-9a-f]{8}")
KEPT = PREFIX + "kept_"


def name(kind: str) -> str:
    return f"{PREFIX}{kind}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def migrations_digest(root: Path) -> str:
    """Names the schema a template holds: every file of alembic/ and alembic.ini, by path
    and content. Migrations import nothing from the application."""
    digest = hashlib.sha256()
    files = [p for p in (root / "alembic").rglob("*") if p.is_file() and p.suffix != ".pyc"]
    for path in sorted([*files, root / "alembic.ini"]):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def kept(kind: str, digest: str) -> str:
    return f"{KEPT}{kind}_{digest}"


def keep(admin_url: str, kind: str, digest: str, build: Callable[[str], None]) -> str:
    """The kept database of this kind and digest, built now if it is not there.

    `build(database)` migrates an empty database. One builder at a time (a server-wide
    advisory lock): the xdist workers of a run all ask at once, and the others then find
    it. The database gets its kept name only when the build has finished, so an
    interrupted build is never used (its timed name goes to the sweep). A new one replaces
    the kept databases of its kind with other digests."""
    template = kept(kind, digest)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (KEPT,))
        try:
            exists = "SELECT 1 FROM pg_database WHERE datname = %s"
            if conn.execute(exists, (template,)).fetchone() is None:
                building = name("template")
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(building)))
                build(building)
                conn.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(building), sql.Identifier(template)
                    )
                )
                others = "SELECT datname FROM pg_database WHERE datname LIKE %s AND datname <> %s"
                for (other,) in conn.execute(
                    others, (kept(kind, "%").replace("_", "\\_"), template)
                ).fetchall():
                    try:
                        conn.execute(
                            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(other))
                        )
                    except psycopg.errors.ObjectInUse:
                        continue
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (KEPT,))
    return template


def stale(names: Iterable[str], now: float) -> list[str]:
    found = []
    for candidate in names:
        if not candidate.startswith(PREFIX) or candidate.startswith(KEPT):
            continue
        timed = _TIMED.fullmatch(candidate)
        if timed is None or int(timed[1]) < now - STALE_AFTER:
            found.append(candidate)
    return sorted(found)


def drop_stale(admin_url: str, now: float | None = None) -> list[str]:
    """Drops the stale databases nobody is connected to; returns their names."""
    dropped = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        rows = conn.execute("SELECT datname FROM pg_database").fetchall()
        for candidate in stale((row[0] for row in rows), time.time() if now is None else now):
            try:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(candidate))
                )
            except psycopg.errors.ObjectInUse:
                continue
            dropped.append(candidate)
    return dropped
