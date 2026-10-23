"""Names of the throwaway databases the tests create, and the sweep of the ones a run
left behind.

A name carries its creation time: `vpslab_<kind>_<unix seconds>_<8 hex>`. A run that is
cut short (a killed hook, a stopped container) cannot drop its databases; the next test
session drops every `vpslab_` database older than STALE_AFTER, and every one without a
time (named before names carried one). Never one in use: DROP without FORCE fails on a
database that has a session, and that one is left for a later sweep.
"""

import re
import time
import uuid
from collections.abc import Iterable

import psycopg
from psycopg import sql

PREFIX = "vpslab_"
# Far longer than any run (make progress included): a live run's databases are younger
STALE_AFTER = 6 * 3600
_TIMED = re.compile(r"vpslab_[a-z]+_(\d+)_[0-9a-f]{8}")


def name(kind: str) -> str:
    return f"{PREFIX}{kind}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def stale(names: Iterable[str], now: float) -> list[str]:
    found = []
    for candidate in names:
        if not candidate.startswith(PREFIX):
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
