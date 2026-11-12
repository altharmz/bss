"""Opt-in pytest attribution for #327. PostgreSQL remains the source of query shapes.

Enabled only by BSS_PLAN_CAPTURE_DIR. No application code or connection behavior changes.
Each xdist process writes its own file; threads share a lock. Bind examples exclude
credentials and opaque strings, and never include connection URLs.
"""

import json
import os
import re
import sys
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from loadtest.plan_sql import explainable, fingerprint, server_query

ROWS: dict[str, dict] = {}
LOCK = threading.Lock()
ORIGINAL = psycopg.Cursor.execute
ORIGINAL_MANY = psycopg.Cursor.executemany


def example(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, (date, datetime)):
        return {"date": value.isoformat()}
    if isinstance(value, timedelta):
        return {"interval_seconds": value.total_seconds()}
    if isinstance(value, UUID):
        return {"uuid": True}
    if isinstance(value, str):
        # Enum/status names and JSON field names are useful. Opaque identifiers,
        # hashes, passwords and tokens are not persisted, even from a test fixture.
        if re.fullmatch(r"[a-zA-Z_ ]{1,40}", value):
            return value
        return {"text": True}
    if isinstance(value, (list, tuple)):
        return {"array": [example(v) for v in value[:3]]}
    return {"type": type(value).__name__}


def attribution(frame):
    locations, origins = [], []
    while frame:
        filename = frame.f_code.co_filename
        if "/app/app/" in filename:
            loc = f"app/{filename.split('/app/app/', 1)[1]}:{frame.f_lineno}"
            locations.append(loc)
            if not origins and frame.f_code.co_name not in {"fetch_one", "fetch_all", "returning"}:
                origins.append(loc)
        frame = frame.f_back
    return locations, origins


def record(cursor, query, params):
    if isinstance(query, sql.Composable):
        query = query.as_string(cursor.connection)
    elif isinstance(query, bytes):
        query = query.decode()
    if not isinstance(query, str) or not explainable(query):
        return
    query, values = server_query(query, params)
    key = fingerprint(query)
    test = os.environ.get("PYTEST_CURRENT_TEST", "session").rsplit(" (", 1)[0]
    with LOCK:
        if key in ROWS and test in ROWS[key]["tests"]:
            ROWS[key]["calls"] += 1
            return
    locations, origins = attribution(sys._getframe(2))
    if not origins:
        return  # fixture-only SQL is exported separately, not application coverage
    with LOCK:
        row = ROWS.setdefault(
            key,
            {
                "query": query,
                "examples": [example(v) for v in values],
                "locations": [],
                "origins": [],
                "tests": [],
                "calls": 0,
            },
        )
        row["calls"] += 1
        row["locations"] = sorted(set(row["locations"]) | set(locations))
        row["origins"] = sorted(set(row["origins"]) | set(origins))
        row["tests"] = sorted(set(row["tests"]) | {test})


def execute(self, query, params=None, *args, **kwargs):
    result = ORIGINAL(self, query, params, *args, **kwargs)
    record(self, query, params)
    return result


def executemany(self, query, params_seq, *args, **kwargs):
    first = []

    def rows():
        for params in params_seq:
            if not first:
                first.append(params)
            yield params

    result = ORIGINAL_MANY(self, query, rows(), *args, **kwargs)
    if first:
        record(self, query, first[0])
    return result


def pytest_configure(config):
    if os.environ.get("BSS_PLAN_CAPTURE_DIR"):
        psycopg.Cursor.execute = execute
        psycopg.Cursor.executemany = executemany


def pytest_sessionfinish(session, exitstatus):
    psycopg.Cursor.execute = ORIGINAL
    psycopg.Cursor.executemany = ORIGINAL_MANY
    out = os.environ.get("BSS_PLAN_CAPTURE_DIR")
    if out:
        path = Path(out)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"capture-{os.getpid()}.json").write_text(json.dumps(ROWS))


def export(out: Path):
    from app.config import settings

    captured, fixture_calls = {}, 0
    for path in sorted((out / "capture").glob("capture-*.json")):
        for row in json.loads(path.read_text()).values():
            if "origins" in row and not row["origins"]:
                fixture_calls += row["calls"]
                continue  # verification SQL passed through a generic fetch helper by a test
            key = fingerprint(row["query"])
            row.setdefault("origins", [])
            if key not in captured:
                captured[key] = row
            else:
                old = captured[key]
                old["calls"] += row["calls"]
                for field in ("tests", "locations", "origins"):
                    old[field] = sorted(set(old[field]) | set(row[field]))
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        stats = conn.execute(
            "SELECT queryid::text, query, sum(calls)::bigint FROM pg_stat_statements"
            " WHERE toplevel GROUP BY queryid, query"
        ).fetchall()
        info = conn.execute("SELECT dealloc FROM pg_stat_statements_info").fetchone()[0]
    groups, excluded = {}, []
    for queryid, query, calls in stats:
        key = fingerprint(query)
        if key not in captured or not explainable(query):
            excluded.append(
                {
                    "queryid": queryid,
                    "query": query,
                    "calls": calls,
                    "reason": "DDL, transaction control, or fixture-only/unattributed",
                }
            )
            continue
        row = groups.setdefault(
            key, {**captured[key], "queryids": [], "server_calls": 0, "normalized_queries": []}
        )
        row["queryids"].append(queryid)
        row["normalized_queries"].append(query)
        row["server_calls"] += calls
    missing = sorted(set(captured) - set(groups))
    for key in missing:
        groups[key] = {
            **captured[key],
            "queryids": [],
            "server_calls": 0,
            "collection_warning": "not matched in pg_stat_statements",
        }
    report = {
        "shapes": list(groups.values()),
        "excluded": excluded,
        "deallocations": info,
        "unmatched_attribution": len(missing),
        "fixture_helper_calls_excluded": fixture_calls,
    }
    (out / "shapes.json").write_text(json.dumps(report, indent=1))
    print(
        f"collected {len(groups)} application shapes; {len(missing)} unmatched; "
        f"{len(excluded)} fixture/utility entries; deallocations={info}"
    )


if __name__ == "__main__":
    export(Path(sys.argv[1]))
