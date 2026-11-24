"""No test module imports another test module (#423). A helper or fixture that more than one
module uses lives in tests/builders/ (or another non-test module): an import of a test module
makes testmon re-run every importer when the test file changes, and renaming or deleting a
test breaks unrelated files. docs/decisions/harness.md, "Test layout"."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

TESTS = Path(__file__).parents[1]


def is_test_module(dotted: str) -> bool:
    return dotted.rsplit(".", 1)[-1].startswith("test_")


def imported_test_modules(source: str) -> list[str]:
    """The test modules `source` imports, as written: `tests.test_x`, `tests.a.test_x`,
    `from tests import test_x`, `from . import test_x` and `from .test_x import y`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if is_test_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if node.module and is_test_module(node.module):
                found.append(module)
            elif node.module is None or node.module == "tests" or node.module.startswith("tests."):
                found += [f"{module}.{a.name}" for a in node.names if a.name.startswith("test_")]
    return found


@pytest.mark.parametrize(
    "line",
    [
        "from tests.tmf.test_events import subscribe",
        "from tests.billing.test_runs import at",
        "from tests import test_mcp_oauth as flow",
        "from tests.billing import test_runs",
        "import tests.tmf.test_events",
        "from .test_events import subscribe",
        "from . import test_events",
        "def f():\n    from tests.tmf.test_events import subscribe",
    ],
)
def test_an_import_of_a_test_module_is_found(line):
    assert imported_test_modules(line) != []


@pytest.mark.parametrize(
    "line",
    [
        "from tests.builders.tmf.events import subscribe",
        "from tests import apidata",
        "from tests.conftest import LEFT_OUT",
        "import pytest",
    ],
)
def test_other_imports_are_not(line):
    assert imported_test_modules(line) == []


def test_no_test_module_imports_another():
    offenders = [
        f"{path.relative_to(TESTS.parent)}: {module}"
        for path in sorted(TESTS.rglob("*.py"))
        for module in imported_test_modules(path.read_text())
    ]
    assert offenders == [], "move the shared names to tests/builders/:\n" + "\n".join(offenders)
