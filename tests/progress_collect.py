"""Backend inventory for the progress tracker (`make progress`, docs/progress/scope.yaml).

Run in the app container: `python -m tests.progress_collect < docs/progress/scope.yaml`.
Prints one JSON object to stdout:

- scope:   scope.yaml, parsed here with the image's pinned PyYAML (the container does not
           mount docs/, so it arrives on stdin);
- openapi: every operation of app.openapi() - FastAPI's public interface, not its router
           internals - as {method, path};
- tables:  the public tables of a throwaway database migrated to head (alembic_version
           excluded), dropped again afterwards;
- capabilities: the tests marked @pytest.mark.capability("<id>") are run here (pytest in
           process, its output on stderr): which ids appear on tests (seen), and which
           had a test pass (passed) or fail (failed). A worker item is proven when it
           passed and did not fail.

Lives in tests/ (like tests/seed.py) because the app container mounts it; it is a tool,
not a test. Everything but the JSON goes to stderr.
"""

import contextlib
import json
import os
import sys
import uuid

import psycopg
import pytest
import yaml
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.config import settings


def openapi_operations() -> list[dict[str, str]]:
    from app.main import app

    paths = app.openapi()["paths"]
    return sorted(
        ({"method": method.upper(), "path": path} for path, ops in paths.items() for method in ops),
        key=lambda op: (op["path"], op["method"]),
    )


def tables_at_head() -> list[str]:
    admin = settings.database_url
    name = f"vpslab_progress_{uuid.uuid4().hex[:8]}"
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


def capabilities() -> dict[str, list[str]]:
    recorder = CapabilityRecorder()
    code = pytest.main(["-m", "capability", "tests"], plugins=[recorder])
    if code not in (
        pytest.ExitCode.OK,
        pytest.ExitCode.TESTS_FAILED,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    ):
        raise SystemExit(f"progress: the capability tests could not run (pytest exit {code})")
    return {
        "seen": sorted(recorder.seen),
        "passed": sorted(recorder.passed),
        "failed": sorted(recorder.failed),
    }


def main() -> int:
    scope = yaml.safe_load(sys.stdin)
    # anything a library prints while importing the app or migrating must not reach the JSON
    with contextlib.redirect_stdout(sys.stderr):
        result = {"scope": scope, "openapi": openapi_operations(), "tables": tables_at_head()}
        result["capabilities"] = capabilities()  # last: pytest imports the whole suite
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
