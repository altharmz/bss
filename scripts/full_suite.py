"""The record of a passing `make test` (the full suite), and what it says about a PR (#395).

CLAUDE.md requires `make test` before a PR that touches billing, migrations or the API.
Nothing showed whether it had run, so a resumed session ran it again (11 minutes). Now a
passing `make test` on a clean tree records `{head, tree, passed_at}` in the worktree's
ignored state, next to the testmon record (RECORD), and `make check-pr` reports
`full_suite`:

- `not-required`: the PR changes no path a backend test may depend on (the hook's own
  selection, hook_selection.backend: docs, harness, UI and CI changes are not).
- `passed`: the record's tree is the PR head's tree, or the only change since the recorded
  head is a merge from `master` that shares no path with the PR (task_git.shared_paths,
  the #390 rule): what came in passed the hook and CI on `master`.
- `stale`: the record names another tree; `missing`: no usable record.

`snapshot` (before the run) notes the clean head and tree; `record` (after a passing run)
writes the record only when the tree is still clean and at that head. A failing run never
reaches `record`; a dirty tree, or one that changed during the run, writes nothing.
"""

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import hook_selection
from task_git import shared_paths

RECORD = ".testmon/full-suite-passed.json"
RUN = ".testmon/full-suite-run.json"
SHA = re.compile(r"[a-f0-9]{40}")


def git(root, *args, check=True):
    # Inside a hook Git exports repository selectors; this reads `root` only.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(  # noqa: S603 - fixed program, argv
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def snapshot(root):
    """{head, tree} of a clean worktree, or None when anything is modified or untracked."""
    if git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        return None
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    return {"head": head, "tree": git(root, "rev-parse", "HEAD^{tree}").stdout.strip()}


def load(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) and SHA.fullmatch(data[key]) for key in ("head", "tree")
    ):
        return None
    return data


def start(root):
    """Before the run: note the clean head and tree, or nothing for a dirty tree."""
    run = Path(root) / RUN
    run.unlink(missing_ok=True)
    taken = snapshot(root)
    if taken is None:
        print("full suite: the tree is not clean; a pass will not be recorded")
        return
    run.parent.mkdir(exist_ok=True)
    run.write_text(json.dumps(taken) + "\n")


def record(root, now=None):
    """After a passing run: write the record when the tree is still the one noted."""
    run = Path(root) / RUN
    taken = load(run)
    run.unlink(missing_ok=True)
    if taken is None or snapshot(root) != {"head": taken["head"], "tree": taken["tree"]}:
        print("full suite: passed, not recorded (the tree was not clean or changed during the run)")
        return
    passed = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    target = Path(root) / RECORD
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps({**taken, "passed_at": passed}) + "\n")
    temporary.replace(target)
    print(f"full suite: passed, recorded for {taken['head']}")


def required(own):
    return any(hook_selection.backend(path) for path in own)


def state(root, head, own):
    """`full_suite` of a PR at `head` whose complete file list is `own` (see the module)."""
    if not required(own):
        return "not-required"
    data = load(Path(root) / RECORD)
    if data is None:
        return "missing"
    found = git(root, "rev-parse", "--verify", "-q", f"{data['head']}^{{tree}}", check=False)
    if found.returncode or found.stdout.strip() != data["tree"]:
        return "missing"
    current = git(root, "rev-parse", "--verify", "-q", f"{head}^{{tree}}", check=False)
    if current.returncode:
        return "stale"
    if current.stdout.strip() == data["tree"]:
        return "passed"
    if git(root, "merge-base", "--is-ancestor", data["head"], head, check=False).returncode:
        return "stale"
    # Every commit since the recorded head, along the branch, is a merge (from master).
    commits = git(root, "rev-list", "--first-parent", "--parents", f"{data['head']}..{head}")
    if any(len(line.split()) < 3 for line in commits.stdout.splitlines()):
        return "stale"
    moved = git(root, "diff", "--no-renames", "--name-only", "-z", data["head"], head).stdout
    return "stale" if shared_paths(own, [p for p in moved.split("\0") if p]) else "passed"


def main(argv):
    root = git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip()
    if argv == ["snapshot"]:
        start(root)
        return 0
    if argv == ["record"]:
        record(root)
        return 0
    print("usage: full_suite.py snapshot|record", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
