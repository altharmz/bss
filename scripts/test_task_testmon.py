#!/usr/bin/env python3
"""scripts/task_testmon.py: the test-selection record saved by finish and seeded by start."""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import task_testmon


def git(*args):
    # Never with inherited GIT_* selectors: the commit hook runs this suite with GIT_DIR
    # set, and `git init <dir>` then re-initialises the hook's repository as bare instead
    # of creating <dir>. That happened to the real repository once (2026-11-11).
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env=task_testmon.git_environment(),
    ).stdout.strip()


def record(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE node (name TEXT)")
        connection.executemany("INSERT INTO node VALUES (?)", [(row,) for row in rows])
    connection.close()


def rows(path):
    connection = sqlite3.connect(path)
    try:
        return [row[0] for row in connection.execute("SELECT name FROM node ORDER BY 1")]
    finally:
        connection.close()


class TestSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.common = self.base / "primary/.git"
        self.common.mkdir(parents=True)
        self.old, self.new = self.base / "old", self.base / "new"
        for tree in (self.old, self.new):
            tree.mkdir()
            git("init", "-q", str(tree))
            (tree / ".gitignore").write_text(".testmon/\n")

    def test_a_saved_record_seeds_the_next_worktree(self):
        record(self.old / task_testmon.DATA, ["test_a", "test_b"])
        self.assertEqual(task_testmon.save(self.old, self.common), "saved")
        self.assertEqual(task_testmon.seed(self.new, self.common), "seeded")
        self.assertEqual(rows(self.new / task_testmon.DATA), ["test_a", "test_b"])
        # only the record: no temporary file is left in either place
        self.assertEqual(
            sorted(p.name for p in (self.new / ".testmon").iterdir()), [".testmondata", "seeded"]
        )
        self.assertEqual(
            [p.name for p in task_testmon.store(self.common).parent.iterdir()], ["testmondata"]
        )

    def test_a_later_task_replaces_the_saved_record(self):
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        record(self.new / task_testmon.DATA, ["test_c"])
        self.assertEqual(task_testmon.save(self.new, self.common), "saved")
        self.assertEqual(rows(task_testmon.store(self.common)), ["test_c"])

    def test_a_task_that_ran_no_test_keeps_the_saved_record(self):
        """Its record is the one it was given: nothing newer to offer (#369)."""
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        task_testmon.seed(self.new, self.common)
        other = self.base / "other"
        record(other / task_testmon.DATA, ["test_newer"])
        self.assertEqual(task_testmon.save(other, self.common), "saved")
        self.assertEqual(task_testmon.save(self.new, self.common), "unchanged")
        self.assertEqual(rows(task_testmon.store(self.common)), ["test_newer"])
        # a run changed the record: it is saved again
        with sqlite3.connect(self.new / task_testmon.DATA) as connection:
            connection.execute("INSERT INTO node VALUES ('test_run')")
        connection.close()
        self.assertEqual(task_testmon.save(self.new, self.common), "saved")
        self.assertEqual(rows(task_testmon.store(self.common)), ["test_a", "test_run"])

    def test_adopting_replaces_the_worktrees_record(self):
        record(self.old / task_testmon.DATA, ["test_master"])
        task_testmon.save(self.old, self.common)
        self.assertEqual(task_testmon.adopt(self.new, self.common), "adopted")
        record_path = self.new / task_testmon.DATA
        self.assertEqual(rows(record_path), ["test_master"])
        with sqlite3.connect(record_path) as connection:
            connection.execute("INSERT INTO node VALUES ('test_own')")
        connection.close()
        record(self.base / "later" / task_testmon.DATA, ["test_later"])
        task_testmon.save(self.base / "later", self.common)
        self.assertEqual(task_testmon.adopt(self.new, self.common), "adopted")
        self.assertEqual(rows(record_path), ["test_later"])
        self.assertEqual(task_testmon.save(self.new, self.common), "unchanged")

    def test_adopting_is_best_effort(self):
        self.assertEqual(task_testmon.adopt(self.new, self.common), "none")
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        (self.new / ".gitignore").write_text("")
        self.assertEqual(task_testmon.adopt(self.new, self.common), "not adopted (not ignored)")
        self.assertFalse((self.new / ".testmon").exists())

    def test_a_sync_adopts_the_record_only_when_backend_code_comes_in(self):
        import task_git

        record(self.old / task_testmon.DATA, ["test_master"])
        task_testmon.save(self.old, self.common)
        identity = ("-c", "user.name=t", "-c", "user.email=t@t")
        git("-C", str(self.new), "add", "-A")
        git("-C", str(self.new), *identity, "commit", "-qm", "base")
        git("-C", str(self.new), "branch", "task")
        common = self.new / ".git"
        shutil.copytree(task_testmon.store(self.common).parent, common / "bss-testmon")
        record(self.new / task_testmon.DATA, ["test_own"])

        def incoming(path):
            git("-C", str(self.new), "checkout", "-q", "-B", "incoming", "task")
            (self.new / path).parent.mkdir(parents=True, exist_ok=True)
            (self.new / path).write_text("x\n")
            git("-C", str(self.new), "add", "-A")
            git("-C", str(self.new), *identity, "commit", "-qm", path)
            git("-C", str(self.new), "checkout", "-q", "task")
            return git("-C", str(self.new), "rev-parse", "incoming")

        with patch("builtins.print") as shown:
            task_git.adopt_test_record(self.new, incoming("scripts/tool.py"))
        shown.assert_not_called()
        self.assertEqual(rows(self.new / task_testmon.DATA), ["test_own"])
        with patch("builtins.print") as shown:
            task_git.adopt_test_record(self.new, incoming("app/main.py"))
        self.assertEqual(shown.call_args.args[0], "sync-task: test-selection record adopted")
        self.assertEqual(rows(self.new / task_testmon.DATA), ["test_master"])

    def test_nothing_saved_means_nothing_seeded(self):
        self.assertEqual(task_testmon.save(self.old, self.common), "none")
        self.assertEqual(task_testmon.seed(self.new, self.common), "none")
        self.assertFalse((self.new / ".testmon").exists())

    def test_a_worktree_that_has_a_record_keeps_it(self):
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        record(self.new / task_testmon.DATA, ["own"])
        self.assertEqual(task_testmon.seed(self.new, self.common), "kept")
        self.assertEqual(rows(self.new / task_testmon.DATA), ["own"])

    def test_a_damaged_record_is_not_saved_and_the_good_one_stays(self):
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        damaged = self.new / task_testmon.DATA
        damaged.parent.mkdir()
        damaged.write_bytes(b"not an sqlite file, long enough to be read as a header" * 4)
        self.assertEqual(task_testmon.save(self.new, self.common), "not saved (DatabaseError)")
        self.assertEqual(rows(task_testmon.store(self.common)), ["test_a"])

    def test_a_damaged_saved_record_seeds_nothing(self):
        saved = task_testmon.store(self.common)
        saved.parent.mkdir()
        saved.write_bytes(b"not an sqlite file, long enough to be read as a header" * 4)
        self.assertEqual(task_testmon.seed(self.new, self.common), "not seeded (DatabaseError)")
        self.assertFalse((self.new / task_testmon.DATA).exists())

    def test_a_worktree_that_does_not_ignore_the_record_is_not_seeded(self):
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        (self.new / ".gitignore").write_text("")
        self.assertEqual(task_testmon.seed(self.new, self.common), "not seeded (not ignored)")
        self.assertFalse((self.new / ".testmon").exists())

    def test_git_selectors_of_a_hook_are_not_inherited(self):
        # as inside the commit hook: GIT_DIR names another repository
        victim = self.base / "victim"
        git("init", "-q", str(victim))
        selectors = {"GIT_DIR": str(victim / ".git"), "GIT_INDEX_FILE": str(victim / ".git/index")}
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        fresh = self.base / "fresh"
        with unittest.mock.patch.dict(os.environ, selectors):
            git("init", "-q", str(fresh))
            (fresh / ".gitignore").write_text(".testmon/\n")
            self.assertEqual(task_testmon.seed(fresh, self.common), "seeded")
            # the victim ignores nothing: with its GIT_DIR inherited the answer would be no
            (self.new / ".gitignore").write_text("")
            self.assertEqual(task_testmon.seed(self.new, self.common), "not seeded (not ignored)")
        self.assertTrue((fresh / ".git").is_dir())
        self.assertEqual(git("-C", str(victim), "config", "--local", "core.bare"), "false")

    def test_symlinks_are_never_followed(self):
        record(self.old / "real/.testmondata", ["test_a"])
        (self.old / ".testmon").symlink_to(self.old / "real")
        self.assertEqual(task_testmon.save(self.old, self.common), "none")
        record(self.base / "other" / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.base / "other", self.common)
        outside = self.base / "outside"
        outside.mkdir()
        (self.new / ".testmon").symlink_to(outside)
        self.assertEqual(task_testmon.seed(self.new, self.common), "kept")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
