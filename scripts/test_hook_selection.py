#!/usr/bin/env python3
"""scripts/hook_selection.py: the test runs the commit hook plans for the staged files."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import hook_selection

SCRIPT = Path(__file__).with_name("hook_selection.py")
UI_TESTS = "tests/test_order_error_codes.py,tests/test_ui_mcp_parity.py,tests/test_ui_requests.py"
USUAL = ["test-smoke", "test-changed"]


def run(directory, *args):
    # Never with inherited GIT_* selectors: the commit hook runs this suite with GIT_DIR
    # set, which would select the hook's repository instead of the fixture's.
    selectors = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--local-env-vars"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    environment = {key: value for key, value in os.environ.items() if key not in selectors}
    return subprocess.run(  # noqa: S603
        list(args), cwd=directory, check=True, capture_output=True, text=True, env=environment
    ).stdout.strip()


class PlanTests(unittest.TestCase):
    def plan(self, *staged, digest="d1", stamp=None):
        return hook_selection.plan(list(staged), digest, stamp)

    def test_python_and_docs_get_smoke_and_affected(self):
        self.assertEqual(self.plan("app/api/bills.py", "tests/test_x.py", "docs/ui.md"), USUAL)

    def test_ui_sources_get_the_tests_that_read_them_not_the_suite(self):
        self.assertEqual(
            self.plan("frontend/src/screens/Bills.tsx", "frontend/src/api/bss.ts"),
            [*USUAL, f"test-files TESTS={UI_TESTS}"],
        )

    def test_other_frontend_files_are_no_backend_data(self):
        self.assertEqual(self.plan("frontend/package.json", "frontend/srcs/x.ts"), USUAL)

    def test_the_scope_file_and_the_parity_script_get_their_readers(self):
        self.assertEqual(
            self.plan("docs/progress/scope.yaml", "scripts/ui_mcp_parity.py"),
            [*USUAL, "test-files TESTS=tests/test_mcp_tools.py,tests/test_ui_mcp_parity.py"],
        )

    def test_a_change_to_the_map_runs_every_mapped_test(self):
        every = sorted({test for tests in hook_selection.DATA.values() for test in tests})
        self.assertEqual(
            self.plan("scripts/hook_selection.py"), [*USUAL, "test-files TESTS=" + ",".join(every)]
        )

    def test_what_any_test_may_depend_on_gets_the_whole_suite(self):
        for path in (
            "spec/TMF620.json",
            "app/worker/run.py",
            "alembic/versions/0001.py",
            "pyproject.toml",
            "requirements-dev.txt",
            "constraints.txt",
            "Dockerfile",
            "compose.override.yaml",
        ):
            with self.subTest(path):
                self.assertEqual(self.plan(path, "frontend/src/a.ts"), ["test-recorded"])

    def test_a_retry_on_the_content_the_suite_ran_on_is_incremental(self):
        self.assertEqual(self.plan("alembic/versions/0001.py", stamp="d1"), USUAL)
        self.assertEqual(
            self.plan("alembic/versions/0001.py", "frontend/src/a.ts", stamp="d1"),
            [*USUAL, f"test-files TESTS={UI_TESTS}"],
        )

    def test_another_content_or_no_stamp_is_the_whole_suite_again(self):
        self.assertEqual(self.plan("alembic/versions/0001.py", stamp="d0"), ["test-recorded"])
        self.assertEqual(self.plan("alembic/versions/0001.py", stamp=None), ["test-recorded"])

    def test_data_key(self):
        self.assertEqual(hook_selection.data_key("frontend/src/a/b.ts"), "frontend/src/")
        self.assertEqual(hook_selection.data_key("frontend/src"), "frontend/src/")
        self.assertEqual(
            hook_selection.data_key("docs/progress/scope.yaml"), "docs/progress/scope.yaml"
        )
        self.assertIsNone(hook_selection.data_key("docs/progress/scope.yaml.bak"))
        self.assertIsNone(hook_selection.data_key("frontend/e2e/a.ts"))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        run(self.repo, "git", "init", "-q")
        self.write("alembic/versions/0001.py", "one\n")
        self.write("app/main.py", "app\n")
        self.write("frontend/src/a.ts", "a\n")
        self.write(".gitignore", ".testmon/\n")
        run(self.repo, "git", "add", "-A")

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def script(self, command):
        return run(self.repo, sys.executable, "-B", str(SCRIPT), command)

    def test_the_digest_follows_only_the_full_suite_files_in_the_index(self):
        first = self.script("digest")
        self.write("app/main.py", "changed\n")
        self.write("frontend/src/a.ts", "changed\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.script("digest"), first)
        self.write("alembic/versions/0001.py", "two\n")
        self.assertEqual(self.script("digest"), first)  # not staged: not in the index
        run(self.repo, "git", "add", "-A")
        self.assertNotEqual(self.script("digest"), first)
        self.write("alembic/versions/0001.py", "one\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.script("digest"), first)

    def test_the_plan_reads_the_staged_files_and_the_stamp(self):
        self.assertEqual(self.script("plan"), "test-recorded")
        self.write(".testmon/full-suite", "")  # a suite that did not run to its end
        self.assertEqual(self.script("plan"), "test-recorded")
        self.write(".testmon/full-suite", self.script("digest") + "\n")
        self.assertEqual(
            self.script("plan"), f"test-smoke\ntest-changed\ntest-files TESTS={UI_TESTS}"
        )
        self.write("alembic/versions/0001.py", "fixed\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.script("plan"), "test-recorded")


class MergeTests(unittest.TestCase):
    """A merge commit: the choices follow the task's own side, not what comes in (#366)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        run(self.repo, "git", "init", "-q", "-b", "master")
        self.write("app/main.py", "one\ntwo\nthree\n")
        self.write("docs/x.md", "docs\n")
        self.commit("base")
        run(self.repo, "git", "checkout", "-q", "-b", "task")

    write = RepositoryTests.write
    script = RepositoryTests.script

    def commit(self, message):
        run(self.repo, "git", "add", "-A")
        run(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)

    def incoming(self, **files):
        """`master` moves on with a migration and the given files."""
        run(self.repo, "git", "checkout", "-q", "master")
        self.write("alembic/versions/0002.py", "two\n")
        for path, text in files.items():
            self.write(path, text)
        self.commit("incoming")
        run(self.repo, "git", "checkout", "-q", "task")

    def merge(self):
        return subprocess.run(  # noqa: S603
            ["git", "merge", "-q", "--no-ff", "--no-commit", "master"],  # noqa: S607
            cwd=self.repo,
            capture_output=True,
            check=False,
        ).returncode

    def test_what_only_comes_in_is_left_out(self):
        self.incoming()
        self.write("docs/x.md", "the task's docs\n")
        self.commit("task")
        self.assertEqual(self.merge(), 0)
        staged = run(self.repo, "git", "diff", "--cached", "--name-only")
        self.assertEqual(staged, "alembic/versions/0002.py")
        self.assertEqual(self.script("paths"), "docs/x.md")
        self.assertEqual(self.script("plan"), "test-smoke\ntest-changed")

    def test_the_tasks_own_side_still_calls_for_the_whole_suite(self):
        self.incoming()
        self.write("app/worker/run.py", "worker\n")
        self.commit("task")
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.script("paths"), "app/worker/run.py")
        self.assertEqual(self.script("plan"), "test-recorded")

    def test_a_conflict_resolved_by_hand_is_included(self):
        self.incoming(**{"app/main.py": "one\nmaster\nthree\n"})
        self.write("app/main.py", "one\ntask\nthree\n")
        self.commit("task")
        self.assertEqual(self.merge(), 1)
        self.write("app/main.py", "one\nboth\nthree\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.script("paths"), "app/main.py")

    def test_without_a_merge_the_staged_paths_are_the_selection(self):
        self.incoming()
        self.write("docs/x.md", "the task's docs\n")
        self.commit("task")
        self.assertEqual(self.script("paths"), "")
        self.write("alembic/versions/0003.py", "three\n")
        self.write("docs/y.md", "more\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.script("paths"), "alembic/versions/0003.py\ndocs/y.md")
        self.assertEqual(self.script("plan"), "test-recorded")


class SuiteStampTests(unittest.TestCase):
    """A script suite the hook runs by path, and the stamp of a run that passed (#366)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        run(self.repo, "git", "init", "-q")
        self.write("scripts/task.py", "one\n")
        self.write("scripts/deploy.sh", "one\n")
        self.write("app/main.py", "app\n")
        run(self.repo, "git", "add", "-A")

    write = RepositoryTests.write

    def script(self, *command):
        return run(self.repo, sys.executable, "-B", str(SCRIPT), *command)

    def states(self):
        return [self.script("suite", target) for target in hook_selection.SUITES]

    def test_a_suite_runs_until_it_passed_on_exactly_this_content(self):
        self.assertEqual(self.states(), ["run", "run"])
        self.script("stamp", "runtime-check")
        self.assertEqual(self.states(), ["passed", "run"])
        self.script("stamp", "deploy-check")
        self.assertEqual(self.states(), ["passed", "passed"])
        self.write("scripts/task.py", "two\n")
        self.assertEqual(self.states(), ["passed", "passed"])  # not staged: not in the index
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states(), ["run", "passed"])  # not a path of the deploy suite
        self.write("scripts/task.py", "one\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states(), ["passed", "passed"])

    def test_no_trigger_path_selected_is_no_suite(self):
        run(self.repo, "git", "rm", "-q", "--cached", "-r", "scripts")
        self.assertEqual(self.states(), ["none", "none"])

    def test_the_trigger_paths(self):
        cases = {
            "scripts/task.py": ["runtime-check"],
            ".claude/hooks/guard.py": ["runtime-check"],
            ".codex/config.toml": ["runtime-check"],
            "Makefile": ["runtime-check", "deploy-check"],
            "scripts/deploy.sh": ["runtime-check", "deploy-check"],
            "scripts/mcp-smoke.sh": ["runtime-check", "deploy-check"],
            "compose.prod.yaml": ["deploy-check"],
            "app/main.py": [],
            "docs/Makefile": [],
        }
        for path, expected in cases.items():
            with self.subTest(path):
                found = [
                    target
                    for target in hook_selection.SUITES
                    if hook_selection.suite_state(target, [path], self.repo) != "none"
                ]
                self.assertEqual(found, expected)

    def test_an_unknown_target_is_refused(self):
        refused = subprocess.run(  # noqa: S603
            [sys.executable, "-B", str(SCRIPT), "stamp", "test-recorded"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((self.repo / ".testmon").exists())


if __name__ == "__main__":
    unittest.main()
