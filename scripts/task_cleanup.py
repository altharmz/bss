"""Remove everything a finished task created; list or clean leftovers of earlier tasks.

One implementation serves `make finish-task` (remove_leftovers) and `make clean-tasks`
(CONFIRM=1 applies it to every finished task that still has leftovers). It removes only
objects carrying the exact task identity: Compose volumes labelled project `bss-<task>`,
the images `bss-<task>-app:dev` / `-web:dev`, the local branch whose tip is the merged
PR's head, and the task's own credential file. The telemetry project, base images, build
cache and other projects are never touched, and nothing here prunes globally.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import start_task
import task_pr
from task_git import require, require_no_git_overrides
from task_runtime import InvalidRuntime, diagnostic
from worker_slots import SlotError, worker_root

TASK = re.compile(r"issue-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*")
PROJECT_LABEL = "com.docker.compose.project"
IMAGE_SUFFIXES = ("app", "web")


def command(root, *args):
    return subprocess.run(  # noqa: S603
        list(args), cwd=root, capture_output=True, text=True, check=True, timeout=60
    ).stdout


def docker(root, *args):
    require(
        not {"DOCKER_HOST", "DOCKER_CONTEXT"} & os.environ.keys(),
        "unset inherited Docker selectors",
    )
    return command(root, "docker", "--context", "default", *args)


def project_of(task):
    require(TASK.fullmatch(task) is not None and len(task) <= 80, "invalid task identity")
    return "bss-" + task


def task_volumes(root, task):
    """(removable names, kept [(name, reason)]) for the task's exact Compose project."""
    project = project_of(task)
    removable, kept = [], []
    names = docker(root, "volume", "ls", "-q", "--filter", f"label={PROJECT_LABEL}={project}")
    for name in names.split():
        labels = json.loads(docker(root, "volume", "inspect", "--format", "{{json .Labels}}", name))
        if (labels or {}).get(PROJECT_LABEL) != project:
            kept.append((name, "project label mismatch"))
        elif docker(root, "ps", "-aq", "--filter", f"volume={name}").split():
            kept.append((name, "in use by a container"))
        else:
            removable.append(name)
    return removable, kept


def task_images(root, task):
    """(removable exact tags, kept [(tag, reason)])."""
    removable, kept = [], []
    for suffix in IMAGE_SUFFIXES:
        tag = f"bss-{task}-{suffix}:dev"
        shown = docker(root, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", tag)
        if tag not in shown.split():
            continue
        if docker(root, "ps", "-aq", "--filter", f"ancestor={tag}").split():
            kept.append((tag, "in use by a container"))
        else:
            removable.append(tag)
    return removable, kept


def branch_tip(primary, task):
    out = command(primary, "git", "for-each-ref", "--format=%(objectname)", f"refs/heads/{task}")
    return out.split()[0] if out.split() else None


def remove_leftovers(root, primary, task, head, removed=None):
    """Remove the task's volumes, images and branch. Idempotent: a retry finds nothing left.

    `removed` is a dict the caller keeps in its checkpoint; it gains the removals as they
    happen, so a partial failure is visible and a rerun continues. Returns (removed, kept).
    """
    removed = removed if removed is not None else {}
    kept = []
    volumes, held = task_volumes(root, task)
    kept += [{"item": f"volume {n}", "reason": r} for n, r in held]
    for name in volumes:
        docker(root, "volume", "rm", name)
        removed.setdefault("volumes", []).append(name)
    images, held = task_images(root, task)
    kept += [{"item": f"image {n}", "reason": r} for n, r in held]
    for tag in images:
        docker(root, "image", "rm", tag)
        removed.setdefault("images", []).append(tag)
    tip = branch_tip(primary, task)
    if tip is None:
        pass
    elif tip == head:
        # Delete exactly this ref at exactly this value; a moved branch is refused by Git.
        command(primary, "git", "update-ref", "-d", f"refs/heads/{task}", head)
        removed["branch"] = task
    else:
        kept.append({"item": f"branch {task}", "reason": "tip differs from the merged PR head"})
    return removed, kept


def remove_credentials(task):
    """Remove the task's own .env (and its directory when nothing else is in it)."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    directory = config / "bss/tasks" / task
    secret = directory / ".env"
    if not directory.is_dir() or directory.is_symlink():
        return False
    if secret.is_file() and not secret.is_symlink():
        secret.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
        return True
    return False


# --- earlier leftovers (make clean-tasks) -------------------------------------------


def task_ids(root, primary):
    """Every task id that still owns a volume, image, local branch or credential directory."""
    found = set()
    for line in docker(
        root, "volume", "ls", "--format", f'{{{{.Label "{PROJECT_LABEL}"}}}}'
    ).splitlines():
        if line.startswith("bss-issue-"):
            found.add(line.removeprefix("bss-"))
    for line in docker(
        root, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "--filter", "reference=bss-*"
    ).splitlines():
        match = re.fullmatch(r"bss-(issue-.+)-(?:app|web):dev", line)
        if match:
            found.add(match.group(1))
    for line in command(
        primary, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads/issue-*"
    ).splitlines():
        found.add(line)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bss/tasks"
    if config.is_dir():
        found.update(p.name for p in config.iterdir() if p.is_dir() and not p.is_symlink())
    return sorted(t for t in found if TASK.fullmatch(t) and len(t) <= 80)


def reserved_tasks(primary):
    tasks = set()
    root = worker_root(primary)
    for active in root.glob("*/active.json") if root.exists() else []:
        try:
            tasks.add(json.loads(active.read_text()).get("task_id"))
        except (OSError, ValueError, AttributeError):
            tasks.add(None)  # unreadable reservation: keep everything to be safe
    return tasks


def finished(root, task):
    """(merged head or None, reason when not finished)."""
    number = task.split("-")[1]
    issue = task_pr.gh(
        root, "issue", "view", number, "--repo", task_pr.REPOSITORY, "--json", "state,projectItems"
    )
    items = [i for i in issue.get("projectItems", []) if i.get("title") == "BSS"]
    if issue.get("state") != "CLOSED" or not (
        len(items) == 1 and items[0].get("status", {}).get("name") == "Done"
    ):
        return None, "issue is not closed and Done"
    prs = task_pr.gh(
        root,
        "pr",
        "list",
        "--repo",
        task_pr.REPOSITORY,
        "--head",
        task,
        "--state",
        "all",
        "--json",
        "number,state,headRefOid",
    )
    if any(p["state"] == "OPEN" for p in prs):
        return None, "open PR"
    merged = [p for p in prs if p["state"] == "MERGED"]
    if not merged:
        return None, "no merged PR for the branch (abandoned work is never deleted)"
    return merged[-1]["headRefOid"], None


def plan(root, primary):
    """{'candidates': [...], 'kept': [...]} for every task that still owns leftovers."""
    trees = {branch for _, branch in start_task.worktrees(primary)}
    reserved = reserved_tasks(primary)
    candidates, kept = [], []
    for task in task_ids(root, primary):
        reason, head = None, None
        project = project_of(task)
        if None in reserved or task in reserved:
            reason = "slot reservation"
        elif task in trees:
            reason = "registered worktree"
        elif docker(root, "ps", "-q", "--filter", f"label={PROJECT_LABEL}={project}").split():
            reason = "running container"
        else:
            head, reason = finished(root, task)
        if reason:
            kept.append({"task": task, "reason": reason})
            continue
        volumes, held_volumes = task_volumes(root, task)
        images, held_images = task_images(root, task)
        tip = branch_tip(primary, task)
        branch = task if tip == head else None
        held = [{"item": n, "reason": r} for n, r in held_volumes + held_images]
        if tip is not None and tip != head:
            held.append({"item": f"branch {task}", "reason": "tip differs from the merged PR head"})
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        credentials = (config / "bss/tasks" / task).is_dir()
        if volumes or images or branch or credentials or held:
            candidates.append(
                {
                    "task": task,
                    "head": head,
                    "volumes": volumes,
                    "images": images,
                    "branch": branch,
                    "credentials": credentials,
                    "kept": held,
                }
            )
    return {"candidates": candidates, "kept": kept}


def clean(root, primary, listing, confirm):
    for item in listing["candidates"]:
        if not confirm:
            continue
        removed, kept = remove_leftovers(root, primary, item["task"], item["head"])
        if remove_credentials(item["task"]):
            removed["credentials"] = True
        item["removed"], item["kept"] = removed, kept
    return listing


def main():
    confirm = os.environ.get("CONFIRM") == "1"
    try:
        require_no_git_overrides()
        root, _common = start_task.primary()
        listing = clean(root, root, plan(root, root), confirm)
        listing["confirmed"] = confirm
        print(json.dumps(listing))
        if not confirm:
            print("clean-tasks: nothing removed; rerun with CONFIRM=1 to remove the candidates")
    except (InvalidRuntime, SlotError, start_task.StartupError) as error:
        print(f"clean-tasks: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"clean-tasks: operation failed ({diagnostic(error)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
