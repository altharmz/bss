"""Test-selection data (pytest-testmon) carried from a finished task to the next worktree.

`make test-changed` runs the tests whose code changed since their last recorded run. A new
worktree has no record, so its first commit ran the whole suite, even for a change that
touches no application code. `make finish-task` saves the merged task's record in the
common Git directory and `make start-task` copies it into the new worktree.

The record is keyed on the content of the code each test ran, not on a commit: a copy that
is older than `master`, or comes from another branch, makes testmon run more tests, never
fewer. Both operations are best effort: any problem leaves the worktree without a record
(the whole suite runs, as before) and is reported in one line, never raised.
"""

import contextlib
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

DATA = ".testmon/.testmondata"
STORE = "bss-testmon/testmondata"


def store(common):
    return Path(common) / STORE


def plain(path):
    return path.is_file() and not path.is_symlink()


def replace_into(target, write):
    """Write through a temporary file in the target's directory; never a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
    os.close(descriptor)
    temporary = Path(name)
    try:
        write(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def backup(source, target):
    """A consistent copy of an SQLite file that passes its own integrity check."""
    with (
        contextlib.closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as original,
        contextlib.closing(sqlite3.connect(target)) as copy,
    ):
        original.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("integrity check failed")


def git_environment():
    """The environment without Git's repository selectors. Inside a hook Git exports
    GIT_DIR and its relatives, and every git command that inherits them acts on the
    hook's repository, whatever directory it names."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def ignored(root):
    """Whether Git ignores the record in this worktree: a seed must never make it dirty."""
    return (
        subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "check-ignore", "-q", DATA],  # noqa: S607 - fixed query
            capture_output=True,
            check=False,
            env=git_environment(),
        ).returncode
        == 0
    )


def save(root, common):
    """Keep the finished task's record for the next worktree; returns what happened."""
    source = Path(root) / DATA
    try:
        if not plain(source) or source.parent.is_symlink():
            return "none"
        replace_into(store(common), lambda temporary: backup(source, temporary))
    except (OSError, sqlite3.Error) as error:
        return f"not saved ({type(error).__name__})"
    return "saved"


def seed(root, common):
    """Give a new worktree the last saved record; returns what happened."""
    saved, target = store(common), Path(root) / DATA
    try:
        if not plain(saved):
            return "none"
        if target.parent.is_symlink() or os.path.lexists(target):
            return "kept"
        if not ignored(root):
            return "not seeded (not ignored)"
        replace_into(target, lambda temporary: backup(saved, temporary))
        target.chmod(0o644)
    except (OSError, sqlite3.Error) as error:
        return f"not seeded ({type(error).__name__})"
    return "seeded"
