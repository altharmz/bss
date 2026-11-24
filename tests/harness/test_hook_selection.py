"""The data files the commit hook maps to their tests (scripts/hook_selection.py, #344):
the map names real files, and a read by a test file it does not list is refused."""

import os
from pathlib import Path

import hook_selection
import pytest
from hook_selection import UnlistedDataRead

# smoke: the hook relies on this map, so the pull-request gate keeps it honest
pytestmark = pytest.mark.smoke

ROOT = str(Path.cwd()) + os.sep
ME = "tests/harness/test_hook_selection.py"


def test_every_mapped_path_and_test_file_exists():
    for key, tests in hook_selection.DATA.items():
        assert Path(key).exists(), key
        assert key.endswith("/") == Path(key).is_dir(), key
        for test in tests:
            assert Path(test).is_file(), test


@pytest.mark.parametrize("key", sorted(hook_selection.DATA))
def test_a_read_by_an_unlisted_test_file_is_refused(key):
    path = Path(key)
    with pytest.raises(UnlistedDataRead, match="add tests/harness/test_hook_selection.py to DATA"):
        if path.is_dir():
            list(path.iterdir())
        else:
            path.read_bytes()


def test_a_listed_test_file_and_other_paths_are_not_refused():
    for key, tests in hook_selection.DATA.items():
        hook_selection.check_read(key, tests[0], ROOT)
        hook_selection.check_read(str(Path(key).resolve()), tests[0], ROOT)
    hook_selection.check_read("tests/conftest.py", ME, ROOT)
    hook_selection.check_read("/etc/hostname", ME, ROOT)
    hook_selection.check_read(3, ME, ROOT)  # an open file descriptor
    hook_selection.check_read("frontend/src/api/bss.ts", None, ROOT)  # no test running
    with pytest.raises(UnlistedDataRead):
        hook_selection.check_read("frontend/src/api/bss.ts", "tests/test_other.py", ROOT)


def test_a_read_of_what_runs_no_backend_test_is_refused():
    """A commit of only the harness, the docs, the UI or CI plans no backend test (#369),
    so no test may read such a path unless the map names the test for it."""
    for path in ("Makefile", "docs/ui.md", "scripts/task_git.py", "frontend/package.json"):
        with pytest.raises(UnlistedDataRead, match="runs no backend test"):
            hook_selection.check_read(path, ME, ROOT)
    for path in ("app/main.py", "alembic.ini", "spec/README.md", "scripts/hook_selection.py"):
        hook_selection.check_read(path, ME, ROOT)
    hook_selection.check_read("docs/ui.md", None, ROOT)  # no test running
    hook_selection.check_read("scripts/__pycache__/ui_mcp_parity.cpython-312.pyc", ME, ROOT)
