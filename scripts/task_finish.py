"""Policy-aware task merge and checkpointed, slot-scoped completion."""

# Executables come from the local development toolchain; all arguments are separate.
# ruff: noqa: S607

import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import start_task
import task_pr
import task_telemetry
from task_cleanup import command, docker, remove_leftovers
from task_git import require, require_no_git_overrides, run
from task_quality import context, paths, query, review
from task_runtime import (
    InvalidRuntime,
    Reexec,
    diagnostic,
    reexec_if_scripts_changed,
    rerun,
    unique_fields,
)
from task_telemetry import record
from worker_slots import SlotError, slot_path


def merged_pr(root, task):
    data = task_pr.find_pr(root, task, allow_merged=True)
    require(data is not None, "task PR is missing")
    return data


def merge(root, task):
    data = merged_pr(root, task)
    if data["state"] == "MERGED":
        require(
            data["headRefOid"] == query(root, "rev-parse", "HEAD").decode().strip(),
            "merged PR head differs from local task",
        )
        return {
            "status": "merged",
            "pr_number": data["number"],
            "url": data["url"],
            "next_command": finish_command(root),
        }, 0
    issue = task_pr.issue_state(root, task)
    outcome, code = task_pr.readiness(root, task, issue)
    if code:
        return outcome, code
    # Repository policy is intentionally squash-only. Refuse changed/ambiguous policy;
    # GitHub still enforces branch rules, approvals and queues at the actual mutation.
    policy = task_pr.gh(root, "api", f"repos/{task_pr.REPOSITORY}")
    if not (
        policy["allow_squash_merge"] is True
        and policy["allow_merge_commit"] is False
        and policy["allow_rebase_merge"] is False
        and policy["archived"] is False
    ):
        return {**outcome, "status": "blocked", "reasons": ["repository-merge-policy"]}, 3
    command(
        root,
        "gh",
        "pr",
        "merge",
        str(outcome["pr_number"]),
        "--repo",
        task_pr.REPOSITORY,
        "--squash",
        "--match-head-commit",
        outcome["head_sha"],
    )
    confirmed = merged_pr(root, task)
    require(
        confirmed["state"] == "MERGED" and confirmed["headRefOid"] == outcome["head_sha"],
        "merge not confirmed; inspect GitHub before retrying (queue may still be pending)",
    )
    return {
        **outcome,
        "status": "merged",
        "reasons": [],
        "next_command": finish_command(root),
    }, 0


def finish_command(root):
    slot = root.parent.name if root.name == "repo" else "<slot>"
    return f"make finish-task SLOT={slot}"


def regular(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), "symlinked cleanup path")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unexpected cleanup file")
    require(info.st_uid == os.getuid(), "cleanup file belongs to another user")
    return info


def load(path):
    regular(path)
    return json.loads(path.read_text(), object_pairs_hook=unique_fields)


def checkpoint(path, data):
    # Never follow or overwrite an unexpected temporary file left by another process.
    temporary = path.with_suffix(".tmp")
    with temporary.open("x") as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def board_done(root, number):
    issue = task_pr.gh(
        root, "issue", "view", str(number), "--repo", task_pr.REPOSITORY, "--json", "projectItems"
    )
    items = [i for i in issue["projectItems"] if i["title"] == "BSS"]
    return len(items) == 1 and items[0]["status"]["name"] == "Done"


def settle_done(root, task, number):
    """The merge and the issue closure are confirmed; the board's automation sets Done.

    Wait a bounded time for it (it usually acts within seconds). If it never does, for
    instance during a GitHub incident, set Done directly rather than block the cleanup.
    """
    wait = int(float(os.environ.get("BSS_BOARD_DONE_WAIT", "60")))
    for attempt in range(wait // 5 + 1):
        if board_done(root, number):
            return
        if attempt < wait // 5:
            time.sleep(5)
    try:
        items = [
            item
            for item in start_task.snapshot()
            if (item.get("content") or {}).get("number") == number
        ]
        require(len(items) == 1, "task project item not found; check the BSS board")
        print(
            f"finish-task: the board did not set #{number} to Done after {wait} s; setting it "
            "(merge and issue closure are confirmed)",
            file=sys.stderr,
        )
        start_task.set_status({"item_id": items[0]["id"]}, start_task.DONE)
    except start_task.StartupError as error:
        raise InvalidRuntime(
            f"task project state is not Done and could not be set: {error}"
        ) from None
    require(board_done(root, number), "task project state is not Done after setting it")


def completed(root, task, head):
    data = merged_pr(root, task)
    require(
        data["state"] == "MERGED" and data["headRefOid"] == head,
        "task PR completion/head not confirmed",
    )
    issue = task_pr.gh(
        root,
        "issue",
        "view",
        task.split("-")[1],
        "--repo",
        task_pr.REPOSITORY,
        "--json",
        "number,state,projectItems",
    )
    require(
        issue["number"] == int(task.split("-")[1]) and issue["state"] == "CLOSED",
        "task issue closure not confirmed; wait for GitHub",
    )
    items = [i for i in issue["projectItems"] if i["title"] == "BSS"]
    if not (len(items) == 1 and items[0]["status"]["name"] == "Done"):
        settle_done(root, task, int(task.split("-")[1]))
    commit = task_pr.gh(
        root,
        "pr",
        "view",
        str(data["number"]),
        "--repo",
        task_pr.REPOSITORY,
        "--json",
        "mergeCommit",
    )["mergeCommit"]["oid"]
    require(
        isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit), "missing merge commit"
    )
    return commit


def synchronize(primary):
    require(
        query(primary, "symbolic-ref", "--short", "HEAD").strip() == b"master",
        "primary must remain on master",
    )
    require(
        not query(primary, "status", "--porcelain=v1", "--untracked-files=all"),
        "primary checkout is dirty",
    )
    command(primary, "git", "fetch", "--prune", "--no-tags", "origin")
    start_task.no_tracked_secrets(primary, "origin/master")
    command(primary, "git", "merge-base", "--is-ancestor", "HEAD", "origin/master")
    command(primary, "git", "merge", "--ff-only", "origin/master")
    require(
        query(primary, "rev-parse", "HEAD") == query(primary, "rev-parse", "origin/master"),
        "primary master is not synchronized",
    )
    require(
        not query(primary, "status", "--porcelain=v1", "--untracked-files=all"),
        "primary checkout changed during synchronization",
    )


def runtime_inventory(root, task):
    """Inspect only bounded ownership fields, never container environment/secrets."""
    project = "bss-" + task
    containers = docker(
        root, "ps", "-aq", "--no-trunc", "--filter", f"label=com.docker.compose.project={project}"
    ).split()
    services = {
        "app",
        "db",
        "mcp",
        "tools",
        "oneoff",
        "web",
        "web-offline",
        "web-preview",
        "e2e",
        "e2e-worker",
    }
    for identity in containers:
        labels = json.loads(
            docker(root, "inspect", "--format", "{{json .Config.Labels}}", identity)
        )
        require(
            labels["com.docker.compose.project"] == project
            and labels["com.docker.compose.service"] in services
            and labels["com.docker.compose.project.working_dir"] == str(root)
            and labels["com.docker.compose.project.config_files"]
            == f"{root}/compose.yaml,{root}/compose.override.yaml",
            "container ownership mismatch; preserved runtime",
        )
    networks = docker(
        root,
        "network",
        "ls",
        "-q",
        "--no-trunc",
        "--filter",
        f"label=com.docker.compose.project={project}",
    ).split()
    for identity in networks:
        labels = json.loads(
            docker(root, "network", "inspect", "--format", "{{json .Labels}}", identity)
        )
        require(
            labels["com.docker.compose.project"] == project
            and labels["com.docker.compose.network"] == "lab",
            "network ownership mismatch",
        )
        attached = json.loads(
            docker(root, "network", "inspect", "--format", "{{json .Containers}}", identity)
        )
        require(
            set(attached or {}) <= set(containers), "unknown container attached to task network"
        )
    return containers, networks


def clean_task(root):
    require(
        not query(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "task worktree is modified; preserved for inspection",
    )
    # Git remove permits ignored files; explicitly refuse unknown ignored material.
    ignored = paths(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    for name in ignored:
        require(
            name == ".task/runtime.json"
            or name.startswith((".testmon/", ".docs/", ".progress/", "frontend/dist/"))
            or any(p in {"__pycache__", ".pytest_cache", ".ruff_cache"} for p in Path(name).parts),
            "unknown ignored task files; preserved for inspection",
        )


def finish(primary, common, slot):
    reservation = load(slot / "active.json")
    task = reservation.get("task_id")
    require(
        reservation == {"version": 1, "task_id": task}
        and type(reservation["version"]) is int
        and isinstance(task, str)
        and len(task) <= 80
        and re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*", task),
        "invalid slot reservation",
    )
    # A bootstrap invocation can load these sources from the task being removed.
    # Telemetry must use the surviving primary checkout for its final result.
    telemetry_root = task_telemetry.ROOT
    task_telemetry.ROOT = primary
    record(task, "finish-task-attempt", 0, 0, slot=slot.name)
    started, result = time.monotonic(), 1
    handed_over = False
    try:
        outcome = finish_steps(primary, common, slot)
        result = 0
        return outcome
    except Reexec:
        handed_over = True
        raise
    finally:
        if not handed_over:
            record(
                task,
                "finish-task",
                result,
                int((time.monotonic() - started) * 1000),
                slot=slot.name,
            )
        task_telemetry.ROOT = telemetry_root


def finish_steps(primary, common, slot):
    root, active = slot / "repo", slot / "active.json"
    reservation = load(active)
    task = reservation.get("task_id")
    require(
        reservation == {"version": 1, "task_id": task}
        and type(reservation["version"]) is int
        and isinstance(task, str)
        and len(task) <= 80
        and re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*", task),
        "invalid slot reservation",
    )
    journal = slot / f"finish-{task}.json"
    state = load(journal) if journal.exists() or journal.is_symlink() else None
    if state is not None:
        require(
            {"version", "task", "head", "runtime_stopped", "secret"}
            <= set(state)
            <= {"version", "task", "head", "runtime_stopped", "secret", "removed"}
            and type(state["version"]) is int
            and state["version"] == 1
            and state["task"] == task
            and re.fullmatch(r"[a-f0-9]{40}", state["head"])
            and type(state["runtime_stopped"]) is bool
            and isinstance(state["secret"], list)
            and len(state["secret"]) == 4
            and all(type(value) is int and value >= 0 for value in state["secret"]),
            "invalid finish checkpoint",
        )
    task_pr.repository(primary)
    trees = start_task.worktrees(primary)
    secret = start_task.secret_file(task, trees, root)
    if state is None:
        require(root.exists() and not root.is_symlink(), "missing task worktree")
        previous = Path.cwd()
        try:
            os.chdir(root)
            actual, owner, identity, name, metadata = context()
            require(
                (actual, owner, identity, name) == (root, primary, task, slot.name),
                "task worktree identity mismatch",
            )
            review(root, primary, task, name, metadata)
        finally:
            os.chdir(previous)
        clean_task(root)
        head = query(root, "rev-parse", "HEAD").decode().strip()
        info = regular(secret)
        state = {
            "version": 1,
            "task": task,
            "head": head,
            "runtime_stopped": False,
            "secret": [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns],
        }
        completed(primary, task, head)
        checkpoint(journal, state)
    record(task, "cleanup-task-attempt", 0, 0, slot=slot.name)
    started = time.monotonic()
    result = 1
    handed_over = False
    try:
        commit = completed(primary, task, state["head"])
        before = query(primary, "rev-parse", "HEAD").decode().strip()
        synchronize(primary)
        # The old scripts ran this far; a fix merged by this task applies from here on.
        reexec_if_scripts_changed(primary, before)
        run(primary, "merge-base", "--is-ancestor", commit, "HEAD")
        # Record task outcome and end timestamps before any disposable resources vanish.
        record(task, "task-completed", 0, 0, slot=slot.name)
        if root.exists():
            require(
                not root.is_symlink() and dict(start_task.worktrees(primary)).get(root) == task,
                "worktree registration mismatch",
            )
            require(
                query(root, "rev-parse", "HEAD").decode().strip() == state["head"],
                "task head changed during cleanup",
            )
            clean_task(root)
            runtime_inventory(root, task)
            command(root, str(root / "scripts/task-compose"), "--profile", "*", "down")
            require(runtime_inventory(root, task) == ([], []), "task runtime removal incomplete")
            state["runtime_stopped"] = True
            checkpoint(journal, state)
            clean_task(root)
            run(primary, "worktree", "remove", "--", str(root))
        require(
            state["runtime_stopped"]
            and not root.is_symlink()
            and root not in dict(start_task.worktrees(primary)),
            "runtime/worktree cleanup not confirmed",
        )
        if secret.exists() or secret.is_symlink():
            info = regular(secret)
            require(
                [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] == state["secret"],
                "task credentials changed; preserved for inspection",
            )
            secret.unlink()
            # An unexpected sibling file keeps the directory in place.
            if not any(secret.parent.iterdir()):
                secret.parent.rmdir()
        # Volumes, images and the branch are disposable development data. The removals are
        # recorded as they happen, so a partial failure resumes from the checkpoint.
        removed = state.setdefault("removed", {})
        try:
            removed, kept = remove_leftovers(primary, primary, task, state["head"], removed)
        finally:
            checkpoint(journal, state)
        completed(primary, task, state["head"])
        synchronize(primary)
        require(load(active) == reservation, "slot reservation changed during cleanup")
        # Retain the task-specific checkpoint for diagnosis, even across slot reuse.
        active.unlink()
        result = 0
        return {
            "status": "finished",
            "task": task,
            "slot": slot.name,
            "removed": removed,
            "kept": kept,
            "next_command": "make start-task BSS_TASK_OWNER="
            + os.environ.get("BSS_TASK_OWNER", "<client>"),
        }
    except Reexec:
        handed_over = True
        raise
    finally:
        if not handed_over:
            record(
                task,
                "cleanup-task",
                result,
                int((time.monotonic() - started) * 1000),
                slot=slot.name,
            )


def main():
    operation = sys.argv[1]
    task = slot = None
    outcome = {}
    result = 1
    started = time.monotonic()
    try:
        require(operation in {"merge-task", "finish-task"}, "unknown operation")
        require_no_git_overrides()
        require(os.environ.get("GH_HOST", "github.com") == "github.com", "unexpected GitHub host")
        if operation == "merge-task":
            root, primary, task, slot, metadata = context()
            record(task, operation + "-attempt", 0, 0, slot=slot)
            review(root, primary, task, slot, metadata)
            require(
                not query(root, "status", "--porcelain=v1", "--untracked-files=all"),
                "task worktree is modified",
            )
            task_pr.repository(root)
            outcome, result = merge(root, task)
        else:
            primary, common = start_task.primary()
            location = slot_path(primary, os.environ.get("BSS_SLOT", ""))
            with contextlib.ExitStack() as stack:
                for path in (common / "start-task.lock", location / "slot.lock"):
                    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    lock = stack.enter_context(os.fdopen(descriptor, "a"))
                    require(os.fstat(lock.fileno()).st_nlink == 1, "unexpected lifecycle lock")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                outcome = finish(primary, common, location)
                result = 0
        print(json.dumps(outcome))
    except Reexec as handover:
        # The locks are released by now; the updated script takes them again.
        result = rerun(handover.root, "task_finish.py", operation)
    except (InvalidRuntime, SlotError, start_task.StartupError) as error:
        print(f"task lifecycle: {error}", file=sys.stderr)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        print(
            f"task lifecycle: operation failed ({diagnostic(error)}); "
            "state preserved for inspection/retry",
            file=sys.stderr,
        )
    finally:
        if task:
            record(
                task,
                operation,
                result,
                int((time.monotonic() - started) * 1000),
                slot=slot,
                pr_number=outcome.get("pr_number"),
                pr_status=outcome.get("status"),
                ci_state=outcome.get("ci"),
            )
    return result


if __name__ == "__main__":
    sys.exit(main())
