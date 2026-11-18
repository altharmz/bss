"""The full-suite record (#395): written only for a passing run on a clean, unchanged tree,
and what it says about a PR head, with real disposable Git."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import full_suite


class FullSuiteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.test")
        self.commit(".gitignore", ".testmon/\n", "ignore")
        self.commit("app/api/a.py", "a = 1\n", "base")
        self.git("checkout", "-q", "-b", "task")
        self.commit("app/api/a.py", "a = 2\n", "task change")
        self.own = ["app/api/a.py"]

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(  # noqa: S603 - test-owned commands in a disposable repository
            ["git", "-C", str(self.root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()

    def commit(self, path, text, message):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git("add", path)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_suite(self, during=None):
        """`make test`'s two calls around a passing run; `during` changes the worktree."""
        with contextlib.redirect_stdout(io.StringIO()) as output:
            full_suite.start(self.root)
            if during:
                during()
            full_suite.record(self.root, now=datetime(2026, 11, 18, 12, tzinfo=UTC))
        return output.getvalue()

    def state(self, head="HEAD"):
        return full_suite.state(self.root, self.git("rev-parse", head), self.own)

    def merge_master(self, path, text):
        self.git("checkout", "-q", "master")
        self.commit(path, text, "master change")
        self.git("checkout", "-q", "task")
        self.git("merge", "-q", "--no-ff", "-m", "Merge origin/master into task branch", "master")

    def test_a_clean_passing_run_is_recorded_and_passes_at_its_tree(self):
        self.assertEqual(self.state(), "missing")
        self.assertIn("recorded for", self.run_suite())
        data = json.loads((self.root / full_suite.RECORD).read_text())
        self.assertEqual(
            data,
            {
                "head": self.git("rev-parse", "HEAD"),
                "tree": self.git("rev-parse", "HEAD^{tree}"),
                "passed_at": "2026-11-18T12:00:00+00:00",
            },
        )
        self.assertFalse((self.root / full_suite.RUN).exists())
        self.assertEqual(self.state(), "passed")

    def test_a_new_commit_to_the_prs_own_files_makes_it_stale(self):
        self.run_suite()
        self.commit("app/api/a.py", "a = 3\n", "another task change")
        self.assertEqual(self.state(), "stale")

    def test_a_merge_from_master_that_shares_no_path_keeps_it_passed(self):
        self.run_suite()
        self.merge_master("app/api/b.py", "b = 1\n")
        self.assertEqual(self.state(), "passed")

    def test_a_merge_from_master_that_shares_a_path_makes_it_stale(self):
        self.run_suite()
        self.merge_master("app/api/c.py", "c = 1\n")
        self.own = ["app/api/a.py", "app/api/c.py"]
        self.assertEqual(self.state(), "stale")

    def test_a_commit_after_the_merge_makes_it_stale(self):
        self.run_suite()
        self.merge_master("app/api/b.py", "b = 1\n")
        self.commit("app/api/d.py", "d = 1\n", "unrelated task commit")
        self.own = ["app/api/a.py", "app/api/d.py"]
        self.assertEqual(self.state(), "stale")

    def test_a_record_of_another_branch_is_stale(self):
        self.run_suite()
        self.git("checkout", "-q", "-b", "other", "master")
        self.commit("app/api/a.py", "a = 9\n", "elsewhere")
        self.assertEqual(self.state(), "stale")

    def test_a_docs_or_harness_pr_needs_no_full_suite(self):
        for own in (["docs/x.md"], ["scripts/task_pr.py", "Makefile", "frontend/src/a.ts"]):
            self.own = own
            self.assertEqual(self.state(), "not-required")
        self.own = ["docs/x.md", "alembic/versions/0001_x.py"]
        self.assertEqual(self.state(), "missing")

    def test_a_dirty_tree_writes_no_record(self):
        (self.root / "app/api/a.py").write_text("a = 5\n")
        self.assertIn("not clean", self.run_suite())
        self.assertFalse((self.root / full_suite.RECORD).exists())
        (self.root / "app/api/a.py").write_text("a = 2\n")
        (self.root / "untracked.py").write_text("")
        self.run_suite()
        self.assertFalse((self.root / full_suite.RECORD).exists())

    def test_a_tree_that_changed_during_the_run_writes_no_record(self):
        self.assertIn(
            "not recorded",
            self.run_suite(lambda: self.commit("app/api/a.py", "a = 4\n", "during the run")),
        )
        self.assertFalse((self.root / full_suite.RECORD).exists())
        self.run_suite(lambda: (self.root / "app/api/a.py").write_text("edited"))
        self.assertFalse((self.root / full_suite.RECORD).exists())

    def test_a_failing_run_writes_no_record(self):
        # `make test` stops at the failing pytest step: `record` is never called.
        with contextlib.redirect_stdout(io.StringIO()):
            full_suite.start(self.root)
        self.assertFalse((self.root / full_suite.RECORD).exists())
        # A later `record` without its own snapshot writes nothing either.
        (self.root / full_suite.RUN).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            full_suite.record(self.root)
        self.assertFalse((self.root / full_suite.RECORD).exists())

    def test_a_malformed_record_is_missing(self):
        target = self.root / full_suite.RECORD
        target.parent.mkdir()
        for text in ("not json", "[]", '{"head": "x", "tree": "y"}'):
            target.write_text(text)
            self.assertEqual(self.state(), "missing")
        target.write_text(json.dumps({"head": "a" * 40, "tree": "b" * 40}))
        self.assertEqual(self.state(), "missing")

    def test_the_cli(self):
        before = os.getcwd()
        try:
            os.chdir(self.root)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(full_suite.main(["snapshot"]), 0)
                self.assertEqual(full_suite.main(["record"]), 0)
            self.assertIn("recorded for", output.getvalue())
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(full_suite.main(["other"]), 2)
        finally:
            os.chdir(before)


if __name__ == "__main__":
    unittest.main()
