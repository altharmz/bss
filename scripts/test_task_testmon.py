#!/usr/bin/env python3
"""scripts/task_testmon.py: the test-selection record saved by finish and seeded by start."""

import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import task_testmon


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
            subprocess.run(["git", "init", "-q", str(tree)], check=True)  # noqa: S603, S607
            (tree / ".gitignore").write_text(".testmon/\n")

    def test_a_saved_record_seeds_the_next_worktree(self):
        record(self.old / task_testmon.DATA, ["test_a", "test_b"])
        self.assertEqual(task_testmon.save(self.old, self.common), "saved")
        self.assertEqual(task_testmon.seed(self.new, self.common), "seeded")
        self.assertEqual(rows(self.new / task_testmon.DATA), ["test_a", "test_b"])
        # only the record: no temporary file is left in either place
        self.assertEqual([p.name for p in (self.new / ".testmon").iterdir()], [".testmondata"])
        self.assertEqual(
            [p.name for p in task_testmon.store(self.common).parent.iterdir()], ["testmondata"]
        )

    def test_a_later_task_replaces_the_saved_record(self):
        record(self.old / task_testmon.DATA, ["test_a"])
        task_testmon.save(self.old, self.common)
        record(self.new / task_testmon.DATA, ["test_c"])
        self.assertEqual(task_testmon.save(self.new, self.common), "saved")
        self.assertEqual(rows(task_testmon.store(self.common)), ["test_c"])

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
