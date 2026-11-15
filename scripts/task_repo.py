"""Git and GitHub commands and the primary checkout's state, shared by the lifecycle scripts."""

import os
import shutil
import subprocess
from pathlib import Path

from task_runtime import gh_detail

STATUS_URL = "https://www.githubstatus.com"
GIT_SELECTORS = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


class StartupError(Exception):
    pass


def run(*args, cwd=None, check=True):
    env = {
        key: value
        for key, value in os.environ.items()
        if args[0] != "git" or key not in GIT_SELECTORS
    }
    result = subprocess.run(  # noqa: S603 - explicit argv, never a shell
        args, cwd=cwd, capture_output=True, text=True, check=False, env=env
    )
    if check and result.returncode:
        # Do not echo subprocess data: URLs and hook output can contain credentials.
        message = f"command failed: {' '.join(args[:3])}"
        if args[0] == "gh":
            detail = gh_detail(result.stderr)
            message += f" ({detail + '; ' if detail else ''}check {STATUS_URL})"
        raise StartupError(message)
    return result


def git(*args, cwd=None):
    return run("git", *args, cwd=cwd).stdout.strip()


def worktrees(root):
    records = git("worktree", "list", "--porcelain", "-z", cwd=root).split("\0\0")
    result = []
    for tree_record in records:
        fields = tree_record.split("\0")
        path = next((s[9:] for s in fields if s.startswith("worktree ")), None)
        branch = next((s[18:] for s in fields if s.startswith("branch refs/heads/")), "")
        if path:
            result.append((Path(path).resolve(), branch))
    return result


def primary():
    if GIT_SELECTORS & os.environ.keys():
        raise StartupError("unset inherited GIT_* selectors before starting a task")
    for executable in ("git", "gh"):
        if not shutil.which(executable):
            raise StartupError(f"{executable} is required")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if Path(git("rev-parse", "--absolute-git-dir")).resolve() != common:
        raise StartupError("run from the primary checkout, not a linked worktree")
    if git("symbolic-ref", "--quiet", "--short", "HEAD") != "master":
        raise StartupError("primary checkout must be on master")
    no_tracked_secrets(root)
    clean(root)
    return root, common


def clean(root):
    if git("status", "--porcelain=v1", "--untracked-files=all", cwd=root):
        raise StartupError("primary checkout must be clean (including staged and untracked files)")


def secret_file(task, trees, destination):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if not config.is_absolute():
        raise StartupError("XDG_CONFIG_HOME must be absolute and external")
    path = config / "bss/tasks" / task / ".env"
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise StartupError("refusing symlinked development secret path")
    roots = [p for p, _ in trees] + [destination]
    if any(path.is_relative_to(root) or config.resolve().is_relative_to(root) for root in roots):
        raise StartupError("development secret path must be external to all worktrees")
    return path


def no_tracked_secrets(root, revision=None):
    names = (
        git("ls-tree", "-r", "--name-only", "-z", revision, cwd=root)
        if revision
        else git("ls-files", "-z", cwd=root)
    )
    if any(Path(name).name in {".env", ".env.prod", ".env.smoke"} for name in names.split("\0")):
        raise StartupError(
            "refusing tracked secret paths; secrets must remain outside task history"
        )


def body_from_file(path):
    file = Path(path)
    if not file.is_absolute() or file.is_symlink() or not file.is_file():
        raise StartupError("BODY_FILE must be an absolute path to a regular file")
    if file.stat().st_size > 65536:
        raise StartupError("BODY_FILE exceeds the 64 KiB limit")
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise StartupError("BODY_FILE is not readable UTF-8 text") from None
