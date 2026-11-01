"""Persistent provider-neutral slot reservations; task runtime identity stays separate."""

import contextlib
import fcntl
import json
import os
import re
import stat
from pathlib import Path


class SlotError(Exception):
    pass


DEFAULT_SLOTS = ("worker1", "worker2", "worker3")
SLOT_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")


def candidates(primary):
    """Default pool plus operator-created slot directories, in stable name order."""
    root = worker_root(primary)
    names = set(DEFAULT_SLOTS)
    if root.exists():
        names.update(p.name for p in root.iterdir() if SLOT_NAME.fullmatch(p.name))
    return sorted(names)


def worker_root(primary):
    root = Path(os.environ.get("BSS_WORKER_ROOT", primary.parent / f"{primary.name}-agents"))
    if not root.is_absolute():
        raise SlotError("BSS_WORKER_ROOT must be absolute")
    for part in (root, *root.parents):
        if part.is_symlink():
            raise SlotError("symlinked worker paths are forbidden")
    return root.resolve()


def slot_path(primary, name):
    if not isinstance(name, str) or not SLOT_NAME.fullmatch(name):
        raise SlotError(
            "slot must be 1–32 lowercase letters, digits or hyphens, starting with a letter"
        )
    root = worker_root(primary)
    slot = root / name
    for part in (slot, *slot.parents):
        if part.is_symlink():
            raise SlotError("symlinked worker paths are forbidden")
    if not slot.resolve().is_relative_to(root.resolve()):
        raise SlotError("slot escapes worker root")
    return slot.resolve()


@contextlib.contextmanager
def reserve(primary, name, trees, recover=None, refresh=None):
    """Keep the lock inode persistent; unknown or changed reservations fail closed."""
    slot = slot_path(primary, name)
    # An enclosing registered checkout must never be used even to create a lock.
    if any(
        slot.is_relative_to(path)
        or (path.is_relative_to(slot) and path != slot / "repo")
        for path, _ in trees
    ):
        raise SlotError("worker slot overlaps a registered worktree")
    slot.mkdir(parents=True, exist_ok=True)
    if slot.stat().st_uid != os.getuid():
        raise SlotError("worker slot belongs to another user")
    lock_path = slot / "slot.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        info = os.fstat(lock.fileno())
        if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SlotError("slot lock has unexpected links")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SlotError("worker slot is locked by another invocation") from None
        for child in slot.iterdir():
            if child.name in {"repo", "active.json", "slot.lock"}:
                continue
            if child.name == "state" and child.is_dir() and not child.is_symlink():
                continue
            if (
                re.fullmatch(r"finish-issue-[1-9][0-9]*-[a-z0-9-]+\.json", child.name)
                and not child.is_symlink()
                and child.is_file()
                and child.stat().st_nlink == 1
            ):
                continue
            raise SlotError(f"unknown slot state: {child.name}; preserved for inspection")
        repo, active = slot / "repo", slot / "active.json"
        if recover is not None and active.exists() and not active.is_symlink():
            recover(slot)
            trees = refresh()
        if any(slot.is_relative_to(path) or path.is_relative_to(slot) for path, _ in trees):
            raise SlotError("worker slot overlaps a registered worktree")
        if any(p.exists() or p.is_symlink() for p in (repo, active)):
            raise SlotError("worker slot is occupied; preserve it and review before release")
        yield slot


def claim(slot, branch):
    active = slot / "active.json"
    payload = json.dumps({"version": 1, "task_id": branch}) + "\n"
    with active.open("x") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
        info = os.fstat(output.fileno())
    return payload, (info.st_dev, info.st_ino)


def undo(slot, owned):
    """Release only our unchanged marker, after Git successfully removed its repo."""
    active = slot / "active.json"
    if (slot / "repo").exists() or (slot / "repo").is_symlink():
        return False
    try:
        info = active.lstat()
        if active.is_symlink() or (info.st_dev, info.st_ino) != owned[1]:
            return False
        if active.read_text() != owned[0]:
            return False
        active.unlink()
        return True
    except OSError:
        return False


def release(primary, name, trees):
    """Explicit acknowledgement after manual worktree removal; never remove task data."""
    slot = slot_path(primary, name)
    repo = slot / "repo"
    if repo.exists() or repo.is_symlink() or any(path == repo for path, _ in trees):
        raise SlotError("remove the task worktree safely before releasing its slot")
    lock_path = slot / "slot.lock"
    with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SlotError("worker slot is locked by another invocation") from None
        if repo.exists() or repo.is_symlink():
            raise SlotError("remove the task worktree safely before releasing its slot")
        active = slot / "active.json"
        info = active.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SlotError("unexpected slot reservation")
        data = json.loads(active.read_text())
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "task_id"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["task_id"], str)
            or len(data["task_id"]) > 80
            or not re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*", data["task_id"])
        ):
            raise SlotError("unexpected slot reservation")
        active.unlink()
