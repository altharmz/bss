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
- MARKED: the tests `make test` and `make test-recorded` leave out (#424): the historical
  migration replays (`migration_history`) and the slowest tests (`slow`). They run again
  when a path they depend on changes: in the whole suite (`make test-recorded MARKS=...`)
  when that is called for, else by themselves (`make test-marked MARKS=...`, recorded for
  testmon, so `make test-changed` after it does not repeat them). A FULL path the stamp
  says the whole suite already ran on brings none back: that run had them.
- DATA: data files only a few tests read. They call for exactly those tests
  (`make test-files`). tests/conftest.py refuses a read of a DATA path by a test file that
  is not listed here, so the map cannot fall behind the tests.

`digest` prints the digest of the FULL files in the index, which `make test-recorded`
writes to the stamp when pytest ran every test (docs/decisions/harness.md).

`paths` prints the paths every choice of the hook is based on (#366): the staged paths, or
on a merge commit the task's own side only. What merely comes in from `master` already
passed the hook and CI there; `make test-changed` still runs the tests the merged code
affects. On a merge commit the task's own FULL files do not call for the whole suite again
when it already ran on them and the merge left them as they were (merge_full_tested, #390).

`plan covered` is the plan when a passing full run is recorded for exactly the staged tree
(full_suite.py covers, #476): only the marked tests the staged paths bring back, which that
run left out. `imports` says whether the hook runs `make import-check` first: `run` when a
selected path can change what the entry points import (IMPORTS), else `none`.

`suite TARGET` says whether the hook runs a harness or deploy suite (SUITES): `none` when
no selected path is one of its trigger paths, `passed` when it already passed on exactly
this content of all its trigger paths (its stamp), else `run`. `stamp TARGET` records that
content after a passing run, so a retry of the commit does not repeat the suite.
`suites` prints the harness suites that depend on the selected paths, or `all` (#369).

Backend tests run only for a change they can depend on (#369): a commit whose selected
paths are all NO_BACKEND (the harness, the docs, the UI, CI) plans no `make test-smoke`
and no `make test-changed`. tests/conftest.py refuses a read of such a path by a test,
unless DATA maps it, so the rule cannot fall behind the tests either. `make test-smoke`
has a stamp like a suite (TARGET `test-smoke`): the content of every backend path.
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

# The marks the default suite leaves out (#424), and the paths that bring each back. Every
# migration_history path is a FULL path too (a case of the hook selection tests keeps it so).
MARKED = {
    "migration_history": re.compile(
        r"^alembic/|^(requirements.*\.txt|constraints.*\.txt|Dockerfile|compose.*\.yaml)$"
    ),
    "slow": re.compile(
        r"^loadtest/"
        r"|^(tests/seed\.py|tests/tmf_gaps\.py|tests/tmf-gaps\.txt"
        r"|app/integrations/network/usage_load\.py)$"
    ),
}

# What can change what the entry points import (make import-check, #476): the code, and
# the image, pins and specs the FULL paths name.
IMPORTS = re.compile(r"^(app|bss_mcp)/")

# A path, or a directory with a trailing slash, and the test files that read it.
DATA = {
    "frontend/src/": (
        "tests/orders/test_order_error_codes.py",
        "tests/internal/test_ui_mcp_parity.py",
        "tests/internal/test_ui_requests.py",
    ),
    "docs/progress/scope.yaml": ("tests/mcp/test_mcp_tools.py",),
    "scripts/ui_mcp_parity.py": ("tests/internal/test_ui_mcp_parity.py",),
}
# A change to the map itself runs every test in it, and with them the read check.
SELF = "scripts/hook_selection.py"
STAMP = ".testmon/full-suite"

# What no backend test depends on: a commit of only these plans no backend test run (#369).
# A DATA path below them still gets its readers, and the map itself is backend code
# (tests/conftest.py imports it).
NO_BACKEND = re.compile(
    r"^(docs|scripts|frontend|design-ref|\.claude|\.codex|\.github)/|^Makefile$|^[^/]*\.md$"
)


def backend(path):
    """Whether a backend test may depend on `path` in a way testmon or DATA does not map."""
    return path == SELF or not NO_BACKEND.search(path)


# The suites the hook runs by path, and the paths that trigger each. The CI harness job
# uses the runtime-check paths (scripts/test_ci_workflow.py keeps the two the same).
SUITES = {
    "runtime-check": re.compile(
        r"^(scripts/|\.claude/|\.codex/|\.github/workflows/|Makefile$|compose\.prod\.yaml$)"
    ),
    # Of the Makefile the deploy suite reads one recipe (DEPLOY_RECIPE): only a change to
    # it runs the suite, not every Makefile change (#369).
    "deploy-check": re.compile(
        r"^(scripts/(deploy\.sh|test_deploy_rollback\.py|run_suites\.py|task-compose"
        r"|billing-status-check\.sh|[a-z-]*smoke[a-z-]*\.(sh|py))"
        r"|compose[a-z.]*\.yaml)$"
    ),
    # The shell cases of the hook's own scripts (make hook-cases).
    "hook-cases": re.compile(r"^scripts/"),
}
# A change to these runs every harness suite: what any suite may depend on.
WHOLE_RUN = ("Makefile", "scripts/run_suites.py", SELF)
# The suites `make runtime-check` leaves out (the Makefile's filter; test_run_suites.py
# keeps the two the same).
NOT_RUNTIME = ("scripts/test_deploy_rollback.py", "scripts/test_telemetry_database.py")
DEPLOY_RECIPE = re.compile(r"^deployed:.*\n((?:\t.*\n)+)", re.M)
# What each stamp follows: the trigger paths of a suite, every backend path for the smoke run.
FOLLOWS = {**{target: pattern.search for target, pattern in SUITES.items()}, "test-smoke": backend}


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
    relative = absolute[len(root) :].replace(os.sep, "/")
    if "/__pycache__/" in relative:
        return  # the bytecode of a source whose own read is checked
    key = data_key(relative)
    if key is not None and reader not in DATA[key]:
        raise UnlistedDataRead(
            f"{reader} reads {os.fsdecode(path)}: the commit hook runs only the tests listed"
            f" for {key!r} when it changes - add {reader} to DATA in scripts/hook_selection.py"
        )
    if key is None and not backend(relative):
        raise UnlistedDataRead(
            f"{reader} reads {os.fsdecode(path)}: the commit hook runs no backend test when"
            f" it changes - map it to {reader} in DATA in scripts/hook_selection.py"
        )


def data_tests(staged):
    tests = set()
    for path in staged:
        if path == SELF:
            tests.update(test for listed in DATA.values() for test in listed)
        elif (key := data_key(path)) is not None:
            tests.update(DATA[key])
    return sorted(tests)


def ci_check(path):
    """Whether a pull request that changes `path` needs the Docker job of CI (lint,
    generated docs, smoke tests): backend code, data the tests read, the sources of the
    generated docs, the workflow itself."""
    return (
        backend(path)
        or data_key(path) is not None
        or path.startswith(("scripts/docs/", ".github/workflows/"))
    )


def marked(paths):
    """The marks of MARKED whose paths are among `paths`."""
    paths = list(paths)
    return [mark for mark, pattern in MARKED.items() if any(pattern.search(p) for p in paths)]


def plan(staged, digest, stamp, merge_tested=False, covered=False):
    """The make invocations for the staged paths. `digest` names the FULL files as they
    are now, `stamp` the content the whole suite last ran to its end on (or None).
    `merge_tested`: a merge commit whose own FULL files the suite already ran on
    (merge_full_tested). `covered`: a passing full run is recorded for exactly the staged
    tree, so only the marked tests it left out remain."""
    if covered:
        marks = marked(staged)
        return ["test-marked MARKS=" + ",".join(marks)] if marks else []
    if any(FULL.search(path) for path in staged) and stamp != digest and not merge_tested:
        marks = marked(staged)
        return ["test-recorded" + (" MARKS=" + ",".join(marks) if marks else "")]
    steps = []
    if any(backend(path) for path in staged):
        marks = marked(path for path in staged if not FULL.search(path))
        steps = ["test-smoke", *(["test-marked MARKS=" + ",".join(marks)] if marks else [])]
        steps.append("test-changed")
    tests = data_tests(staged)
    if tests:
        steps.append("test-files TESTS=" + ",".join(tests))
    return steps


def imports(selected):
    return any(IMPORTS.search(path) or FULL.search(path) for path in selected)


def git(*args):
    return subprocess.run(  # noqa: S603 - fixed program, fixed arguments
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def index_digest(follows, extra=""):
    """Names the content of every file in the index `follows` accepts: mode, blob and path."""
    digest = hashlib.sha256(extra.encode())
    for entry in git("ls-files", "-s", "-z").split("\0"):
        if entry and follows(entry.split("\t", 1)[1]):
            digest.update(entry.encode() + b"\0")
    return digest.hexdigest()


def full_digest():
    return index_digest(FULL.search)


def tree_digest(revision, follows):
    """index_digest of a commit's tree: the entries `ls-files -s` lists for it, same order."""
    digest = hashlib.sha256(b"")
    for entry in git("ls-tree", "-r", "-z", "--full-tree", revision).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, _, blob = meta.split()
        if follows(path):
            digest.update(f"{mode} {blob} 0\t{path}".encode() + b"\0")
    return digest.hexdigest()


def merge_full_tested(selected, stamp):
    """On a merge commit, whether the task's own FULL files need no whole-suite run (#390).

    The selection leaves out what only came in; a FULL path it keeps is either the task's
    own, as its last commit has it, or one both sides changed (or a hand-resolved
    conflict). The whole suite need not run again when every kept FULL path is unchanged
    from HEAD and the suite ran to its end on HEAD's FULL content (the stamp): the incoming
    FULL files passed the hook and CI on master. Before #390 the incoming files changed
    the digest and every sync merge of a task with a FULL change ran the whole suite."""
    if stamp is None or merge_head() is None:
        return False
    full = {path for path in selected if FULL.search(path)}
    moved = set(git("diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD").split("\0"))
    return not full & moved and stamp == tree_digest("HEAD", FULL.search)


def deploy_recipe(revision):
    """The Makefile recipe the deploy suite reads, as `revision` (or the index, "") has it."""
    shown = subprocess.run(  # noqa: S603 - fixed program, fixed arguments
        ["git", "show", f"{revision}:Makefile"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    found = DEPLOY_RECIPE.search(shown.stdout) if shown.returncode == 0 else None
    return found.group(1) if found else ""


def suite_digest(target):
    extra = deploy_recipe("") if target == "deploy-check" else ""
    return index_digest(FOLLOWS[target], extra)


def read_stamp(root, stamp=STAMP):
    try:
        return (Path(root) / stamp).read_text().strip() or None
    except OSError:
        return None


def merge_head():
    """The commit being merged in, or None when no merge is in progress."""
    found = subprocess.run(  # noqa: S603 - fixed program, fixed arguments
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.strip() if found.returncode == 0 else None


def selected_paths():
    """The paths the hook chooses its checks from. Usually the staged paths. On a merge
    commit, the paths whose index content differs from the commit being merged in: the
    task's own changes and the conflicts resolved by hand, not what only came in."""
    against = [] if merge_head() is None else ["MERGE_HEAD"]
    return [p for p in git("diff", "--cached", "--name-only", "-z", *against).split("\0") if p]


def named(text):
    """What a script's source names: file names (`task.py`, `guard-cases.txt`), and Python
    modules by an import or a quoted module name (fixtures copy `f"{name}.py"` in loops)."""
    files = {token.rstrip(".") for token in re.findall(r"[\w.-]+", text)}
    modules = re.findall(r"^\s*(?:from|import)\s+(\w+)", text, re.M)
    modules += re.findall(r"[\"'](\w+)[\"']", text)
    return files | {f"{module}.py" for module in modules}


def harness_suites(selected, root):
    """The `make runtime-check` suites that depend on the selected paths, or None for all
    of them (#369). A suite depends on a file its source names, or the source of a script
    it reaches through such names (an imported module, a script copied into its fixture).
    The Makefile, the runner, this file, and a path no suite names call for every suite:
    an unknown dependency never runs less. CI runs every suite on a pull request."""
    names = {
        path.name: named(path.read_text(errors="replace"))
        for path in (Path(root) / "scripts").glob("*.py")
    }
    reached = {}
    for suite in (name for name in names if name.startswith("test_")):
        seen, queue = {suite}, [suite]
        while queue:
            for other in names[queue.pop()] & names.keys() - seen:
                seen.add(other)
                queue.append(other)
        reached[suite] = set().union(*(names[name] for name in seen)) | {suite}
    chosen = set()
    for path in selected:
        if not SUITES["runtime-check"].search(path):
            continue
        if path in WHOLE_RUN:
            return None
        found = {suite for suite, known in reached.items() if path.rsplit("/", 1)[-1] in known}
        if not found:
            return None
        chosen |= found
    return sorted({f"scripts/{suite}" for suite in chosen} - set(NOT_RUNTIME))


def triggered(target, selected):
    if any(FOLLOWS[target](path) for path in selected):
        return True
    if target == "deploy-check" and "Makefile" in selected:
        before = "HEAD" if merge_head() is None else "MERGE_HEAD"
        return deploy_recipe("") != deploy_recipe(before)
    return False


def suite_state(target, selected, root):
    if not triggered(target, selected):
        return "none"
    if read_stamp(root, f".testmon/{target}") == suite_digest(target):
        return "passed"
    return "run"


def write_suite_stamp(target, root):
    stamp = Path(root) / ".testmon" / target
    stamp.parent.mkdir(exist_ok=True)
    stamp.write_text(suite_digest(target) + "\n")


def main(argv):
    if argv == ["digest"]:
        print(full_digest())
        return 0
    if argv == ["ci-check"]:
        changed = sys.stdin.read().split("\n")
        print("run=" + ("true" if any(ci_check(path) for path in changed if path) else "false"))
        return 0
    if argv == ["paths"]:
        sys.stdout.write("".join(path + "\n" for path in selected_paths()))
        return 0
    root = git("rev-parse", "--show-toplevel").strip()
    if argv == ["imports"]:
        print("run" if imports(selected_paths()) else "none")
        return 0
    if argv in (["plan"], ["plan", "covered"]):
        selected, stamp = selected_paths(), read_stamp(root)
        tested = merge_full_tested(selected, stamp)
        steps = plan(selected, full_digest(), stamp, tested, covered=argv == ["plan", "covered"])
        print("\n".join(steps))
        return 0
    if argv == ["suites"]:
        suites = harness_suites(selected_paths(), root)
        print("all" if suites is None else " ".join(suites))
        return 0
    if len(argv) == 2 and argv[0] in ("suite", "stamp") and argv[1] in FOLLOWS:
        if argv[0] == "suite":
            print(suite_state(argv[1], selected_paths(), root))
        else:
            write_suite_stamp(argv[1], root)
        return 0
    print(
        "usage: hook_selection.py plan [covered]|digest|paths|imports|suites|ci-check|suite TARGET"
        "|stamp TARGET"
        f" (TARGET: {', '.join(FOLLOWS)})",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
