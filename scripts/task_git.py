"""Repository-owned task commits and publication; never rewrite history."""

# Fixed Git executable, resolved from the local development toolchain.
# ruff: noqa: S607

import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

from task_quality import context, forbidden, paths, query, review
from task_runtime import InvalidRuntime
from task_telemetry import record


def require(condition, message):
    if not condition:
        raise InvalidRuntime(message)


# Inherited variables that only change how Git presents or edits text, never what it does.
PRESENTATION = {"GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR"}


def run(root, *args):
    # Hooks start their own normal Make invocation. Outer command-line assignments
    # must not override its tests, and inherited dry-run flags must not skip them.
    controls = {
        "MAKEFLAGS",
        "MAKEOVERRIDES",
        "MFLAGS",
        "GNUMAKEFLAGS",
        "MAKEFILES",
        "MAKELEVEL",
        "MESSAGE",
        "MESSAGE_FILE",
        "FILES",
        "FILES_FILE",
        "DRY_RUN",
    }
    environ = {key: value for key, value in os.environ.items() if key not in controls}
    subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(root), *args], check=True, env=environ
    )


def hooks(root):
    configured = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(root), "config", "--get", "core.hooksPath"], capture_output=True
    )
    require(configured.returncode == 1, "custom core.hooksPath is unsupported; run make hooks")
    common = query(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    dispatcher = runpy.run_path(str(root / "scripts/install-hooks.py"))["dispatcher"]
    for name in ("pre-commit", "pre-push"):
        path = Path(common) / "hooks" / name
        require(
            path.is_file()
            and not path.is_symlink()
            and os.access(path, os.X_OK)
            and path.read_bytes() == dispatcher(name).encode(),
            "repository hooks missing or unexpected; run make hooks",
        )


def text_input(inline, from_file):
    """A value given inline (MESSAGE, FILES) or as an absolute file path (MESSAGE_FILE, ...)."""
    value, path = os.environ.get(inline, ""), os.environ.get(from_file, "")
    if not path:
        return value
    require(not value, f"give {inline} or {from_file}, not both")
    source = Path(path)
    require(
        source.is_absolute() and source.is_file() and not source.is_symlink(),
        f"{from_file} must be an absolute path to a regular file",
    )
    require(source.stat().st_size <= 65536, f"{from_file} exceeds the 64 KiB limit")
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise InvalidRuntime(f"{from_file} is not readable UTF-8 text") from None


def commit(root):
    dry_run = os.environ.get("DRY_RUN", "0")
    require(dry_run in {"0", "1"}, "DRY_RUN must be 0 or 1")
    message = text_input("MESSAGE", "MESSAGE_FILE")
    require(message.strip(), "MESSAGE or MESSAGE_FILE is required")
    selected = json.loads(text_input("FILES", "FILES_FILE") or "[]")
    require(
        isinstance(selected, list) and selected and all(isinstance(n, str) for n in selected),
        "FILES must be an explicit JSON array of repository-relative filenames",
    )
    changed = (
        set(paths(root, "diff", "--no-renames", "--name-only", "-z"))
        | set(paths(root, "diff", "--cached", "--no-renames", "--name-only", "-z"))
        | set(paths(root, "ls-files", "--others", "--exclude-standard", "-z"))
    )
    require(len(set(selected)) == len(selected), "duplicate FILES")
    require(
        all(n in changed and not forbidden(n) for n in selected),
        "FILES must name changed safe files",
    )
    require(
        all(not (root / n).is_dir() for n in selected),
        "FILES cannot include directories or submodules",
    )
    staged = set(paths(root, "diff", "--cached", "--no-renames", "--name-only", "-z"))
    unmerged = set(paths(root, "diff", "--name-only", "--diff-filter=U", "-z"))
    require(staged <= set(selected), "unselected files already staged; inspect the index")
    require(
        not (staged - unmerged).intersection(
            paths(root, "diff", "--no-renames", "--name-only", "-z")
        ),
        "partially staged files; inspect the index before committing",
    )
    # An unmerged index has stages 1/2/3, not a partially staged source version.
    # Explicit FILES authorizes staging the user's edited conflict resolution.
    unstaged = [name for name in selected if name not in staged or name in unmerged]
    if dry_run == "1":
        print("task commit: dry run validated; index, HEAD and hooks unchanged")
        return None
    if unstaged:
        run(root, "--literal-pathspecs", "add", "--", *unstaged)
    logs = query(root, "rev-parse", "--absolute-git-dir").decode().strip()
    print(f"task commit: the hook keeps its full output in {logs}/pre-commit-logs/", flush=True)
    run(root, "commit", "-m", message)
    return query(root, "rev-parse", "HEAD").decode().strip()


def remote_configuration(root, task):
    urls = query(root, "remote", "get-url", "--all", "origin").splitlines()
    pushurls = query(root, "remote", "get-url", "--push", "--all", "origin").splitlines()
    require(len(urls) == 1 and urls == pushurls, "origin must have one matching fetch/push URL")
    for key, expected in (("remote", b"origin"), ("merge", f"refs/heads/{task}".encode())):
        result = subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(root), "config", "--get-all", f"branch.{task}.{key}"],
            capture_output=True,
        )
        require(
            result.returncode == 1 or result.stdout.splitlines() == [expected],
            "unexpected upstream",
        )
    mirror = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(root), "config", "--bool", "--get", "remote.origin.mirror"],
        capture_output=True,
    )
    require(
        mirror.returncode == 1 or mirror.stdout.strip() == b"false", "mirror remote is forbidden"
    )


def sync(root, task):
    require(
        not query(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "commit intended changes before make sync-task",
    )
    remote_configuration(root, task)
    run(root, "fetch", "--no-tags", "--refmap=", "origin", "refs/heads/master")
    base = query(root, "rev-parse", "FETCH_HEAD").decode().strip()
    require(
        not any(
            forbidden(name) and not name.startswith("design-ref/")
            for name in paths(root, "ls-tree", "-r", "--name-only", "-z", base)
        ),
        "origin/master contains a protected or forbidden path",
    )
    ancestors = query(root, "rev-list", f"HEAD..{base}").splitlines()
    if not ancestors:
        return
    # Never rewrite published history. A conflict or hook failure preserves the
    # merge state for inspection and explicit file resolution through make commit.
    run(root, "merge", "--no-ff", "--no-commit", base)
    run(root, "commit", "-m", "Merge origin/master into task branch")


def push(root, task):
    remote_configuration(root, task)
    refs = query(root, "ls-remote", "--heads", "origin", "refs/heads/master", f"refs/heads/{task}")
    remote = dict(line.decode().split()[::-1] for line in refs.splitlines())
    require("refs/heads/master" in remote, "origin master is missing")
    run(root, "fetch", "--no-tags", "--refmap=", "origin", "refs/heads/master")
    base = query(root, "rev-parse", "FETCH_HEAD").decode().strip()
    require(base == remote["refs/heads/master"], "origin changed during inspection; retry")
    require(
        subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(root), "merge-base", "--is-ancestor", base, "HEAD"],
            capture_output=True,
        ).returncode
        == 0,
        "task is behind origin/master; run make sync-task, then retry",
    )
    ref = f"refs/heads/{task}"
    if ref in remote:
        run(root, "fetch", "--no-tags", "--refmap=", "origin", ref)
        tip = query(root, "rev-parse", "FETCH_HEAD").decode().strip()
        require(tip == remote[ref], "remote task branch changed during inspection; retry")
        run(root, "merge-base", "--is-ancestor", tip, "HEAD")
    for revision in query(root, "rev-list", f"{base}..HEAD").decode().splitlines():
        require(
            not any(
                forbidden(n)
                for n in paths(
                    root,
                    "diff-tree",
                    "--root",
                    "-m",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    revision,
                )
            ),
            "outgoing commit touches a protected path",
        )
    run(
        root,
        "push",
        "--no-follow-tags",
        "--recurse-submodules=no",
        "--set-upstream",
        "origin",
        f"HEAD:{ref}",
    )
    return query(root, "rev-parse", "HEAD").decode().strip()


def main():
    operation = sys.argv[1]
    event_operation = (
        "commit-dry-run"
        if operation == "commit" and os.environ.get("DRY_RUN") == "1"
        else operation
    )
    task = slot = identity = None
    result = 1
    started = time.monotonic()
    try:
        require(operation in {"commit", "push", "sync-task"}, "unknown operation")
        refused = sorted(
            key for key in os.environ if key.startswith("GIT_") and key not in PRESENTATION
        )
        require(
            not refused,
            f"unset inherited GIT_* overrides: {', '.join(refused)} "
            f"({', '.join(sorted(PRESENTATION))} are presentation-only)",
        )
        root, primary, task, slot, data = context()
        record(task, event_operation + "-attempt", 0, 0, slot=slot)
        review(root, primary, task, slot, data)
        hooks(root)
        if operation == "sync-task":
            sync(root, task)
        else:
            identity = commit(root) if operation == "commit" else push(root, task)
        result = 0
        if identity:
            print(f"task {operation}: {identity}")
    except InvalidRuntime as error:
        print(f"task {operation}: {error}", file=sys.stderr)
    except (OSError, ValueError, subprocess.CalledProcessError):
        # CalledProcessError may include the commit message in argv; never echo it.
        print(
            f"task {operation}: Git/hook failed or refused; inspect state; no cleanup performed",
            file=sys.stderr,
        )
    finally:
        if task:
            record(
                task,
                event_operation,
                result,
                int((time.monotonic() - started) * 1000),
                slot=slot,
                commit_hash=identity if result == 0 else None,
            )
    return result


if __name__ == "__main__":
    sys.exit(main())
