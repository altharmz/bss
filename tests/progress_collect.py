"""Backend inventory for the progress tracker (`make progress`, docs/progress/scope.yaml).

Run in the app container: `python -m tests.progress_collect < docs/progress/scope.yaml`.
Prints one JSON object to stdout:

- scope:   scope.yaml, parsed here with the image's pinned PyYAML (the container does not
           mount docs/, so it arrives on stdin);
- openapi: every operation of app.openapi() - FastAPI's public interface, not its router
           internals - as {method, path};
- tables:  the public tables of a throwaway database migrated to head (alembic_version
           excluded), dropped again afterwards;
- capabilities: the whole test suite is run here, once (pytest in process, its output on
           stderr). Of the tests marked @pytest.mark.capability("<id>"): which ids
           appear on tests (seen), and which had a test pass (passed) or fail (failed).
           A worker item is proven when it passed and did not fail.
- tested:  from the same run, what the passing tests exercised (tests/conftest.py
           records it when BSS_PROGRESS_RECORD=1): api - the app.openapi() operations
           their requests were routed to (method + path template); tables - the tables
           their throwaway databases wrote, rows inserted or updated
           (pg_stat_user_tables.n_tup_ins + n_tup_upd, read before each database is
           dropped; a one-row settings table is only ever updated). A test that failed
           in any phase counts for nothing, whatever it sent or wrote before failing.

Lives in tests/ (like tests/seed.py) because the app container mounts it; it is a tool,
not a test. Everything but the JSON goes to stderr.
"""

import contextlib
import json
import os
import sys

import psycopg
import pytest
import yaml
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings
from tests import scratch_db


def openapi_operations() -> list[dict[str, str]]:
    from app.main import app

    paths = app.openapi()["paths"]
    return sorted(
        ({"method": method.upper(), "path": path} for path, ops in paths.items() for method in ops),
        key=lambda op: (op["path"], op["method"]),
    )


def tables_at_head() -> list[str]:
    admin = settings.database_url
    name = scratch_db.name("progress")
    ident = sql.Identifier(name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(ident))
    url = admin.rsplit("/", 1)[0] + f"/{name}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads it
    try:
        command.upgrade(Config("alembic.ini"), "head")
        with psycopg.connect(url) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename <> 'alembic_version' ORDER BY tablename"
            ).fetchall()
        return [row[0] for row in rows]
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))


class CapabilityRecorder:
    """pytest plugin: the capability ids on each test, and how those tests ended."""

    def __init__(self) -> None:
        self.ids: dict[str, set[str]] = {}
        self.seen: set[str] = set()
        self.passed: set[str] = set()
        self.failed: set[str] = set()

    def pytest_collection_modifyitems(self, items) -> None:
        for item in items:
            ids = {arg for mark in item.iter_markers("capability") for arg in mark.args}
            if ids:
                self.ids[item.nodeid] = ids
                self.seen |= ids

    def pytest_runtest_logreport(self, report) -> None:
        ids = self.ids.get(report.nodeid, set())
        if report.failed:  # in setup, call or teardown
            self.failed |= ids
        elif report.when == "call" and report.passed:
            self.passed |= ids


class TestedRecorder:
    """pytest plugin: what the passing tests exercised - the requests they sent to the app
    and the tables their database got rows in (tests/conftest.py records both per test
    when BSS_PROGRESS_RECORD=1, in the test's user_properties). A test counts only if its
    call passed and no phase failed."""

    def __init__(self) -> None:
        self.passed: set[str] = set()
        self.failed: set[str] = set()
        self.requests: set[tuple[str, str]] = set()
        self.tables: set[str] = set()

    def pytest_runtest_logreport(self, report) -> None:
        if report.failed:
            self.failed.add(report.nodeid)
        elif report.when == "call" and report.passed:
            self.passed.add(report.nodeid)
        if report.when != "teardown":
            return
        if report.nodeid not in self.passed or report.nodeid in self.failed:
            return
        for name, value in report.user_properties:
            if name == "progress":
                self.requests |= {(method, path) for method, path in value["requests"]}
                self.tables |= set(value["tables"])


def match_operation(
    operations: list[dict[str, str]], method: str, path: str
) -> dict[str, str] | None:
    """The app.openapi() operation a request was routed to: the method and the path
    template ({param} = one path segment). A literal segment wins over a parameter
    (/billingRun/catchUp before /billingRun/{run_id}); None for no operation (a 404)."""
    candidates = []
    for op in operations:
        if op["method"] != method:
            continue
        template = op["path"].split("/")
        parts = path.split("/")
        if len(template) != len(parts):
            continue
        pairs = zip(template, parts, strict=True)
        if all(t == p or (t.startswith("{") and t.endswith("}")) for t, p in pairs):
            candidates.append((op["path"].count("{"), op["path"], op))
    return min(candidates)[2] if candidates else None


def run_suite(operations: list[dict[str, str]], args: list[str]) -> dict:
    """Run the test suite once (pytest in process, its output on stderr) with both
    recorders: the worker capabilities and what the passing tests exercised."""
    capabilities, tested = CapabilityRecorder(), TestedRecorder()
    os.environ["BSS_PROGRESS_RECORD"] = "1"  # read by tests/conftest.py at import
    try:
        code = pytest.main(["-p", "no:cacheprovider", *args], plugins=[capabilities, tested])
    finally:
        del os.environ["BSS_PROGRESS_RECORD"]
    if code not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        raise SystemExit(f"progress: the test suite could not run (pytest exit {code})")
    exercised = {
        (op["method"], op["path"])
        for method, path in tested.requests
        if (op := match_operation(operations, method, path)) is not None
    }
    return {
        "capabilities": {
            "seen": sorted(capabilities.seen),
            "passed": sorted(capabilities.passed),
            "failed": sorted(capabilities.failed),
        },
        "tested": {
            "api": [{"method": m, "path": p} for m, p in sorted(exercised)],
            "tables": sorted(tested.tables),
        },
    }


def main() -> int:
    scope = yaml.safe_load(sys.stdin)
    # anything a library prints while importing the app or migrating must not reach the JSON
    with contextlib.redirect_stdout(sys.stderr):
        result = {"scope": scope, "openapi": openapi_operations(), "tables": tables_at_head()}
        # last: pytest imports the whole suite. Extra arguments narrow the run (the
        # negative proofs); make progress passes none and runs every test.
        result |= run_suite(result["openapi"], sys.argv[1:] or ["tests"])
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
