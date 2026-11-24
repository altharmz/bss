"""The smoke subset (make test-smoke, the CI gate on pull requests and master) stays defined:
the marker is registered, so a typo in a mark is not silently a no-op, and the areas the
gate exists for each keep at least one marked test. The marks make test leaves out
(`migration_history`, `slow`, #424) are registered too, never on a smoke test, and every
migration replay carries `migration_history` unless tests/conftest.py keeps it."""

import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import KEPT_REPLAYS, LEFT_OUT, mark_errors

pytestmark = pytest.mark.smoke

TESTS = Path(__file__).parents[1]
AREAS = {
    "billing core": "billing/test_billing_core.py",
    "TMF contract": "tmf/test_contract_validator.py",
    "migrations": "schema/test_migrations.py",
    "MCP": "mcp/test_mcp_server.py",
}


@pytest.mark.parametrize("marker", ["smoke", *LEFT_OUT])
def test_the_marker_is_registered(marker):
    pyproject = TESTS.parent / "pyproject.toml"
    markers = tomllib.loads(pyproject.read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert any(registered.startswith(f"{marker}:") for registered in markers)


def item(name, *marks, fixtures=()):
    return SimpleNamespace(
        nodeid=f"tests/test_x.py::{name}[a]",
        name=f"{name}[a]",
        originalname=name,
        fixturenames=fixtures,
        iter_markers=lambda: [SimpleNamespace(name=mark) for mark in marks],
    )


@pytest.mark.parametrize("left_out", LEFT_OUT)
def test_a_smoke_test_cannot_be_left_out_of_the_default_run(left_out):
    assert mark_errors([item("test_a", "smoke"), item("test_b", left_out)]) == []
    assert mark_errors([item("test_c", "smoke", left_out)]) == [
        f"tests/test_x.py::test_c[a]: a smoke test cannot also be {left_out}"
    ]


def test_a_migration_replay_is_migration_history_unless_kept():
    replay = "temp_db_url", "db_conn"
    assert mark_errors([item("test_a", "migration_history", fixtures=replay)]) == []
    assert mark_errors([item("test_b", fixtures=("db_conn",))]) == []
    (error,) = mark_errors([item("test_c", fixtures=replay)])
    assert error.startswith("tests/test_x.py::test_c[a] replays migrations: mark it")


def test_the_kept_replays_are_the_head_and_round_trip_checks():
    assert (
        "tests/schema/test_migrations.py::test_alembic_upgrade_head_on_clean_database"
        in KEPT_REPLAYS
    )
    assert all(re.fullmatch(r"tests/\w+/test_\w+\.py::test_\w+", name) for name in KEPT_REPLAYS)


def test_the_collected_tests_pass_the_check(request):
    assert mark_errors(request.session.items) == []


@pytest.mark.parametrize("area", AREAS)
def test_every_gate_area_keeps_a_marked_test(area):
    assert "pytest.mark.smoke" in (TESTS / AREAS[area]).read_text()
