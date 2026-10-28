"""The smoke subset (make test-smoke, the CI gate on pull requests and master) stays defined:
the marker is registered, so a typo in a mark is not silently a no-op, and the areas the
gate exists for each keep at least one marked test."""

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

TESTS = Path(__file__).parent
AREAS = {
    "billing core": "test_billing_core.py",
    "TMF contract": "test_contract_validator.py",
    "migrations": "test_migrations.py",
    "MCP": "test_mcp_server.py",
}


def test_the_smoke_marker_is_registered():
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    markers = tomllib.loads(pyproject.read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("smoke:") for marker in markers)


@pytest.mark.parametrize("area", AREAS)
def test_every_gate_area_keeps_a_marked_test(area):
    assert "pytest.mark.smoke" in (TESTS / AREAS[area]).read_text()
