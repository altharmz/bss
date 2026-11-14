"""Which backend test runs a commit calls for: the plan of scripts/pre-commit.sh (#344).

`plan` prints the make invocations for the staged files, one per line. The usual plan is
the smoke subset plus the tests the change affects (pytest-testmon follows the Python code
each test ran). testmon cannot see two kinds of change:

- FULL: files any test may depend on (the specs the app and the response validator read,
  the dependency pins, the image and compose files), `app/worker/` (its tests run it in
  subprocesses) and `alembic/` (too costly to trust to selection). They call for the whole
  suite, recorded (`make test-recorded`), unless that suite already ran to its end on
  exactly this content of the FULL files (the stamp): then a retry runs what testmon
  selects, which includes every test that failed.
- DATA: data files only a few tests read. They call for exactly those tests
  (`make test-files`). tests/conftest.py refuses a read of a DATA path by a test file that
  is not listed here, so the map cannot fall behind the tests.

`digest` prints the digest of the FULL files in the index, which `make test-recorded`
writes to the stamp when pytest ran every test (docs/decisions/harness.md).
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

FULL = re.compile(
    r"^(spec|app/worker|alembic)/"
    r"|^(pyproject\.toml|requirements.*\.txt|constraints.*\.txt|Dockerfile|compose.*\.yaml)$"
)

# A path, or a directory with a trailing slash, and the test files that read it.
DATA = {
    "frontend/src/": (
        "tests/test_order_error_codes.py",
        "tests/test_ui_mcp_parity.py",
        "tests/test_ui_requests.py",
    ),
    "docs/progress/scope.yaml": ("tests/test_mcp_tools.py",),
    "scripts/ui_mcp_parity.py": ("tests/test_ui_mcp_parity.py",),
}
# A change to the map itself runs every test in it, and with them the read check.
SELF = "scripts/hook_selection.py"
STAMP = ".testmon/full-suite"


def data_key(path):
    """The DATA entry `path` (repository-relative) belongs to, or None."""
    for key in DATA:
        if path == key.rstrip("/") or (key.endswith("/") and path.startswith(key)):
            return key
    return None


class UnlistedDataRead(Exception):
    pass


def check_read(path, reader, root):
    """Refuses a read of a DATA path under `root` (absolute, with a trailing separator)
    by `reader`, a test file DATA does not list for it. tests/conftest.py calls this for
    every file a test opens or directory it lists."""
    if reader is None or not isinstance(path, (str, bytes, os.PathLike)):
        return
    absolute = os.path.abspath(os.fsdecode(path))
    if not absolute.startswith(root):
        return
    key = data_key(absolute[len(root) :].replace(os.sep, "/"))
    if key is not None and reader not in DATA[key]:
        raise UnlistedDataRead(
            f"{reader} reads {os.fsdecode(path)}: the commit hook runs only the tests listed"
            f" for {key!r} when it changes - add {reader} to DATA in scripts/hook_selection.py"
        )


def data_tests(staged):
    tests = set()
    for path in staged:
        if path == SELF:
            tests.update(test for listed in DATA.values() for test in listed)
        elif (key := data_key(path)) is not None:
            tests.update(DATA[key])
    return sorted(tests)


def plan(staged, digest, stamp):
    """The make invocations for the staged paths. `digest` names the FULL files as they
    are now, `stamp` the content the whole suite last ran to its end on (or None)."""
    if any(FULL.search(path) for path in staged) and stamp != digest:
        return ["test-recorded"]
    steps = ["test-smoke", "test-changed"]
    tests = data_tests(staged)
    if tests:
        steps.append("test-files TESTS=" + ",".join(tests))
    return steps


def git(*args):
    return subprocess.run(  # noqa: S603 - fixed program, fixed arguments
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def full_digest():
    """Names the content of every FULL file in the index: mode, blob and path."""
    digest = hashlib.sha256()
    for entry in git("ls-files", "-s", "-z").split("\0"):
        if entry and FULL.search(entry.split("\t", 1)[1]):
            digest.update(entry.encode() + b"\0")
    return digest.hexdigest()


def read_stamp(root):
    try:
        return (Path(root) / STAMP).read_text().strip() or None
    except OSError:
        return None


def main(argv):
    if argv == ["digest"]:
        print(full_digest())
        return 0
    if argv == ["plan"]:
        staged = [p for p in git("diff", "--cached", "--name-only", "-z").split("\0") if p]
        root = git("rev-parse", "--show-toplevel").strip()
        print("\n".join(plan(staged, full_digest(), read_stamp(root))))
        return 0
    print("usage: hook_selection.py plan|digest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
