"""The record of a passing full backend run, and what it says about a PR (#395, #476).

CLAUDE.md requires `make test` before a PR that touches billing, migrations or the API.
Nothing showed whether it had run, so a resumed session ran it again (11 minutes). Now a
passing full run records `{head, tree, passed_at}` in the worktree's ignored state, next to
the testmon record (RECORD): `tree` names the content the run tested, `head` the commit it
was based on. `make check-pr` reports `full_suite`:

- `not-required`: the PR changes no path a backend test may depend on (the hook's own
  selection, hook_selection.backend: docs, harness, UI and CI changes are not).
- `passed`: the PR head's tree is the recorded tree, or the newest commit on the branch
  with that tree is followed only by merges from `master` that share no path with the PR
  (task_git.shared_paths, the #390 rule): what came in passed the hook and CI on `master`.
- `stale`: the record names other content; `missing`: no usable record.

Two runs write it. `make test`: `snapshot` (before the run) notes HEAD and the tree of
what is on disk, tracked and untracked files alike (content_tree), and `record` (after a
passing run) writes them when both are unchanged. So `make test` on the work still to be
committed counts for the commit that holds exactly that content (#476). The commit hook:
`covers` tells it a passing run exists for exactly the staged tree, so it runs no suite
again, and `record-index` records its own whole-suite run (`make test-recorded`) once it
passed on the staged tree. A failing run never reaches `record`; content that changed
during the run writes nothing.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import hook_selection
from task_git import shared_paths

RECORD = ".testmon/full-suite-passed.json"
RUN = ".testmon/full-suite-run.json"
SHA = re.compile(r"[a-f0-9]{40}")


def git(root, *args, check=True, index=None):
    # Inside a hook Git exports repository selectors; this reads `root` only (and the
    # throwaway `index` when given).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(  # noqa: S603 - fixed program, argv
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def content_tree(root):
    """The tree of what is on disk: HEAD with every tracked and untracked (not ignored)
    file as the worktree has it, written through a throwaway index."""
    with tempfile.TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        git(root, "read-tree", "HEAD", index=index)
        git(root, "add", "-A", index=index)
        return git(root, "write-tree", index=index).stdout.strip()


def snapshot(root):
    """{head, tree}: HEAD and the content on disk, or None without a HEAD."""
    found = git(root, "rev-parse", "-q", "--verify", "HEAD", check=False)
    if found.returncode:
        return None
    return {"head": found.stdout.strip(), "tree": content_tree(root)}


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


def write(root, taken, now=None):
    passed = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    target = Path(root) / RECORD
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps({**taken, "passed_at": passed}) + "\n")
    temporary.replace(target)


def start(root):
    """Before the run: note HEAD and the content on disk."""
    run = Path(root) / RUN
    run.unlink(missing_ok=True)
    taken = snapshot(root)
    if taken is None:
        print("full suite: no commit yet; a pass will not be recorded")
        return
    run.parent.mkdir(exist_ok=True)
    run.write_text(json.dumps(taken) + "\n")


def record(root, now=None):
    """After a passing run: write the record when HEAD and the content are still the ones noted."""
    run = Path(root) / RUN
    taken = load(run)
    run.unlink(missing_ok=True)
    if taken is None or snapshot(root) != {"head": taken["head"], "tree": taken["tree"]}:
        print("full suite: passed, not recorded (the content or HEAD changed during the run)")
        return
    write(root, taken, now)
    print(f"full suite: passed, recorded for tree {taken['tree']} on {taken['head']}")


def hook_git(*args):
    """git as the commit hook sees it: its own index (GIT_INDEX_FILE) and repository."""
    return subprocess.run(  # noqa: S603 - fixed program, argv
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def staged():
    """{head, tree} of the commit being made: HEAD and the staged tree."""
    return {"head": hook_git("rev-parse", "HEAD"), "tree": hook_git("write-tree")}


def covers(root):
    """Whether a passing full run is recorded for exactly the staged tree."""
    data = load(Path(root) / RECORD)
    return data is not None and data["tree"] == staged()["tree"]


def record_index(root, now=None):
    """The hook's own whole-suite run passed on the staged tree: record it."""
    taken = staged()
    write(root, taken, now)
    print(f"full suite: passed in the hook, recorded for tree {taken['tree']}")


def required(own):
    return any(hook_selection.backend(path) for path in own)


def kind(root, name):
    found = git(root, "cat-file", "-t", name, check=False)
    return found.stdout.strip() if found.returncode == 0 else None


def state(root, head, own):
    """`full_suite` of a PR at `head` whose complete file list is `own` (see the module)."""
    if not required(own):
        return "not-required"
    data = load(Path(root) / RECORD)
    if data is None or kind(root, data["head"]) != "commit" or kind(root, data["tree"]) != "tree":
        return "missing"
    current = git(root, "rev-parse", "--verify", "-q", f"{head}^{{tree}}", check=False)
    if current.returncode:
        return "stale"
    if current.stdout.strip() == data["tree"]:
        return "passed"
    if git(root, "merge-base", "--is-ancestor", data["head"], head, check=False).returncode:
        return "stale"
    # The commit the run tested: the newest on the branch since the recorded head (or that
    # head itself) with the recorded tree. Every commit after it must be a merge (from master).
    log = git(root, "log", "--first-parent", "--format=%H %T %P", f"{data['head']}..{head}")
    lines = [line.split() for line in log.stdout.splitlines()]
    lines.append([data["head"], git(root, "rev-parse", f"{data['head']}^{{tree}}").stdout.strip()])
    tested = next((i for i, fields in enumerate(lines) if fields[1] == data["tree"]), None)
    if tested is None or any(len(fields) < 4 for fields in lines[:tested]):
        return "stale"
    base = lines[tested][0]
    moved = git(root, "diff", "--no-renames", "--name-only", "-z", base, head).stdout
    return "stale" if shared_paths(own, [p for p in moved.split("\0") if p]) else "passed"


def main(argv):
    root = git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip()
    if argv == ["snapshot"]:
        start(root)
        return 0
    if argv == ["record"]:
        record(root)
        return 0
    if argv == ["covers"]:
        if covers(root):
            print("full suite: passed on exactly the staged content already")
            return 0
        return 1
    if argv == ["record-index"]:
        record_index(root)
        return 0
    print("usage: full_suite.py snapshot|record|covers|record-index", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
