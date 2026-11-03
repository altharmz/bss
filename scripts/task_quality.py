"""Repository-owned task quality commands. Never read secret contents."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from task_runtime import InvalidRuntime, diagnostic, git, unique_fields, validate_metadata
from task_telemetry import record


def query(root, *args):
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        check=True,
    )
    return result.stdout


def paths(root, *args):
    return [os.fsdecode(p) for p in query(root, *args).split(b"\0") if p]


def changed_paths(root):
    """Include committed, staged, unstaged and new files; unknown base means all checks."""
    try:
        base = query(root, "merge-base", "HEAD", "origin/master").decode().strip()
        return (
            set(paths(root, "diff", "--no-renames", "--name-only", "-z", base))
            | set(paths(root, "diff", "--cached", "--no-renames", "--name-only", "-z", base))
            | set(paths(root, "ls-files", "--others", "--exclude-standard", "-z"))
        )
    except subprocess.CalledProcessError:
        return None


def needs_ui(names):
    # Unknown paths and empty/unknown change sets retain the comprehensive check.
    return not names or any(
        not name.startswith(("scripts/", "docs/", ".claude/"))
        and name not in {"CLAUDE.md", "AGENTS.md"}
        for name in names
    )


def format_targets(names):
    """Backend always; the UI formatter only when UI sources changed or the base is unknown."""
    if names is None or any(n.startswith(("frontend/", "spec/")) for n in names):
        return ["fmt", "web-fmt"]
    print("task quality: no changes under frontend/ or spec/; skipping web-fmt (UI formatter)")
    return ["fmt"]


def check_stages(names):
    stages = ["check-preflight", "lint", "test", "docs-check"]
    if needs_ui(names):
        stages.extend(("web-lint", "web-test", "web-e2e"))
    return [*stages, "guard-check"]


def run_stages(root, stages):
    failures = []
    for stage in stages:
        print(f"task quality: running {stage}", flush=True)
        if subprocess.run(["make", stage], cwd=root).returncode:  # noqa: S603, S607
            failures.append(stage)
    print(
        "task quality: " + ("failed: " + ", ".join(failures) if failures else "all stages passed")
    )
    return int(bool(failures))


def web_test(root):
    names = changed_paths(root)
    stages = ["web-test-once"]
    if os.environ.get("CI") or names is None or any(n.startswith("frontend/src/") for n in names):
        stages.append("web-test-timezone")
    return run_stages(root, stages)


def context():
    if {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    } & os.environ.keys():
        # Git hooks supply selectors; these entry points deliberately require a clean shell.
        raise InvalidRuntime("unset inherited GIT_* selectors for task quality commands")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if Path(git("rev-parse", "--absolute-git-dir")).resolve() == common:
        raise InvalidRuntime("use a linked task worktree; primary is administration-only")
    metadata = root / ".task/runtime.json"
    if metadata.is_symlink() or metadata.parent.is_symlink():
        raise InvalidRuntime("symlinked runtime metadata")
    data = json.loads(metadata.read_text(), object_pairs_hook=unique_fields)
    task, _ = validate_metadata(data, git("symbolic-ref", "--quiet", "--short", "HEAD"))
    trees = git("worktree", "list", "--porcelain", "-z").split("\0")
    primary = Path(next(row[9:] for row in trees if row.startswith("worktree ")))
    if query(primary, "symbolic-ref", "--short", "HEAD").strip() != b"master":
        raise InvalidRuntime("primary checkout must be on master")
    slot = None
    active = root.parent / "active.json"
    if root.name == "repo" or active.exists() or active.is_symlink():
        if root.name != "repo":
            raise InvalidRuntime("slot worktree must be at <slot>/repo")
        if active.is_symlink() or root.parent.is_symlink():
            raise InvalidRuntime("symlinked slot reservation")
        reservation = json.loads(active.read_text(), object_pairs_hook=unique_fields)
        if (
            reservation != {"version": 1, "task_id": task}
            or type(reservation["version"]) is not int
        ):
            raise InvalidRuntime("slot reservation does not match task")
        slot = root.parent.name
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", slot):
            raise InvalidRuntime("invalid worker slot")
    return root, primary, task, slot, data


def forbidden(path):
    parts = Path(path).parts
    return (
        any(p.startswith(".env") and p != ".env.example" for p in parts)
        or any(
            p
            in {
                ".task",
                ".deployed",
                ".deployed.tmp",
                ".e2e",
                ".shots",
                ".progress",
                ".docs",
                ".testmon",
                "node_modules",
                "__pycache__",
                "backups",
            }
            for p in parts
        )
        or path.startswith("design-ref/")
        or path.endswith((".pem", ".key", ".tar.gz"))
    )


def review(root, primary, task, slot, data, check_whitespace=True, verbose=False):
    """Validate the task state; print the context lines only for review-task or VERBOSE=1."""
    show = verbose or os.environ.get("VERBOSE") == "1"
    if show:
        print(
            json.dumps(
                {
                    "task": task,
                    "issue": int(task.split("-")[1]),
                    "slot": slot,
                    "worktree": str(root),
                    "runtime": data,
                }
            )
        )
    problems = []
    for label, args in (
        ("modified", ("diff", "--name-only", "-z")),
        ("staged", ("diff", "--cached", "--name-only", "-z")),
        ("untracked", ("ls-files", "--others", "--exclude-standard", "-z")),
    ):
        names = paths(root, *args)
        if show:
            print(json.dumps({label: names}))
        if any(forbidden(name) for name in names):
            problems.append("protected or forbidden path in " + label)
    # Include ignored secret filenames; don't open them or traverse ignored build trees.
    local = paths(
        root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        ".",
        ":(exclude)**/node_modules/**",
        ":(exclude).venv/**",
    )
    if any(
        any(p.startswith(".env") and p != ".env.example" for p in Path(n).parts)
        or n.endswith((".pem", ".key"))
        for n in local
    ):
        problems.append("suspicious ignored task-local secret file")
    if any(forbidden(n) and not n.startswith("design-ref/") for n in paths(root, "ls-files", "-z")):
        problems.append("protected or forbidden tracked path")
    if paths(root, "ls-files", "--cached", "--ignored", "--exclude-standard", "-z"):
        problems.append("ignored/generated artifact present in the index")
    if query(primary, "status", "--porcelain=v1", "--untracked-files=all"):
        problems.append("primary checkout is dirty")
    for args in (("diff", "--check"), ("diff", "--cached", "--check")):
        if not check_whitespace:
            continue
        # Never echo diff output: it can contain secret values.
        result = subprocess.run(  # noqa: S603 - fixed read-only Git commands
            ["git", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
        )
        if result.returncode:
            problems.append("git " + " ".join(args) + " failed")
    if problems:
        raise InvalidRuntime("; ".join(problems))


def main():
    operation = sys.argv[1]
    if operation == "web-test":
        return web_test(Path.cwd())
    started = time.monotonic()
    task = slot = None
    result = 1
    try:
        root, primary, task, slot, data = context()
        review(
            root,
            primary,
            task,
            slot,
            data,
            check_whitespace=operation != "format",
            verbose=operation == "review-task",
        )
        if operation == "review-task":
            result = 0
        else:
            if operation == "check":
                os.environ["HYPOTHESIS_PROFILE"] = "ci"
                result = run_stages(root, check_stages(changed_paths(root)))
            else:
                for target in format_targets(changed_paths(root)):
                    result = subprocess.run(["make", target], cwd=root).returncode  # noqa: S603, S607
                    if result:
                        break
    except (InvalidRuntime, OSError, ValueError, subprocess.CalledProcessError) as error:
        result = 1
        # Our own errors are repository-controlled; others get a bounded cause only.
        shown = error if isinstance(error, InvalidRuntime) else diagnostic(error)
        print(f"task quality: {shown}", file=sys.stderr)
    finally:
        if task:
            record(task, operation, result, int((time.monotonic() - started) * 1000), slot=slot)
    return result


if __name__ == "__main__":
    sys.exit(main())
