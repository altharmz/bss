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
SCRIPT_SUITES = ("runtime-check", "deploy-check")
# A CI runner has no git identity, and a merge needs one even with --no-commit. The merge
# cases run without the developer's own, so a missing one fails here and not only in CI.
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@t")
NO_IDENTITY = {
    "PATH": os.environ["PATH"],
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


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

    def test_ui_sources_get_only_the_tests_that_read_them(self):
        self.assertEqual(
            self.plan("frontend/src/screens/Bills.tsx", "frontend/src/api/bss.ts"),
            [f"test-files TESTS={UI_TESTS}"],
        )
        self.assertEqual(
            self.plan("frontend/src/api/bss.ts", "app/api/bills.py"),
            [*USUAL, f"test-files TESTS={UI_TESTS}"],
        )

    def test_the_scope_file_and_the_parity_script_get_only_their_readers(self):
        self.assertEqual(
            self.plan("docs/progress/scope.yaml", "scripts/ui_mcp_parity.py"),
            ["test-files TESTS=tests/test_mcp_tools.py,tests/test_ui_mcp_parity.py"],
        )

    def test_what_no_backend_test_depends_on_plans_no_backend_test(self):
        for path in (
            "scripts/task_git.py",
            "scripts/pre-commit.sh",
            "docs/decisions/harness.yaml",
            "frontend/package.json",
            "frontend/srcs/x.ts",
            ".claude/hooks/guard.py",
            ".codex/hooks.json",
            ".github/workflows/ci.yml",
            "design-ref/a.png",
            "Makefile",
            "README.md",
        ):
            with self.subTest(path):
                self.assertEqual(self.plan(path), [])
        self.assertEqual(self.plan(), [])

    def test_everything_else_is_backend(self):
        for path in (
            "app/api/bills.py",
            "bss_mcp/server.py",
            "tests/conftest.py",
            "loadtest/seed.py",
            "alembic.ini",
            ".env.example",
            "app/README.md",
            "docs.py",
            "an/unknown/path",
        ):
            with self.subTest(path):
                self.assertEqual(self.plan(path, "scripts/task_git.py"), USUAL)

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
        run(self.repo, "git", *IDENTITY, "commit", "-qm", message)

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
            ["git", *IDENTITY, "merge", "-q", "--no-ff", "--no-commit", "master"],  # noqa: S607
            cwd=self.repo,
            capture_output=True,
            check=False,
            env=NO_IDENTITY,
        ).returncode

    def test_what_only_comes_in_is_left_out(self):
        self.incoming()
        self.write("docs/x.md", "the task's docs\n")
        self.commit("task")
        self.assertEqual(self.merge(), 0)
        staged = run(self.repo, "git", "diff", "--cached", "--name-only")
        self.assertEqual(staged, "alembic/versions/0002.py")
        self.assertEqual(self.script("paths"), "docs/x.md")
        self.assertEqual(self.script("plan"), "")  # no backend test for the incoming code

    def test_a_backend_change_of_the_task_runs_the_usual_plan_on_a_merge(self):
        self.incoming()
        self.write("app/main.py", "one\ntwo\nthree\nfour\n")
        self.commit("task")
        self.assertEqual(self.merge(), 0)
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
        self.write(".gitignore", ".testmon/\n")
        run(self.repo, "git", "add", "-A")

    write = RepositoryTests.write

    def script(self, *command):
        return run(self.repo, sys.executable, "-B", str(SCRIPT), *command)

    def states(self, *targets):
        return [self.script("suite", target) for target in targets or SCRIPT_SUITES]

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
        self.assertEqual(self.states(*hook_selection.FOLLOWS), ["none", "none", "none", "run"])
        run(self.repo, "git", "rm", "-q", "--cached", "-r", "app", ".gitignore")
        self.assertEqual(self.states("test-smoke"), ["none"])

    def test_the_hook_cases_follow_every_script(self):
        self.assertEqual(self.states("hook-cases"), ["run"])
        self.script("stamp", "hook-cases")
        self.assertEqual(self.states("hook-cases"), ["passed"])
        self.write("scripts/deploy.sh", "two\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states("hook-cases"), ["run"])

    def test_the_smoke_run_follows_the_backend_content(self):
        self.assertEqual(self.states("test-smoke"), ["run"])
        self.script("stamp", "test-smoke")
        self.assertEqual(self.states("test-smoke"), ["passed"])
        self.write("scripts/task.py", "two\n")  # no backend path
        self.write("docs/x.md", "docs\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states("test-smoke"), ["passed"])
        self.write("app/main.py", "changed\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states("test-smoke"), ["run"])

    def test_of_the_makefile_only_the_deploy_recipe_runs_the_deploy_suite(self):
        commit = ("git", *IDENTITY, "commit", "-qm", "x")
        self.write("Makefile", "deployed:\n\techo one\n\nother:\n\techo a\n")
        run(self.repo, "git", "add", "-A")
        run(self.repo, *commit)
        self.script("stamp", "deploy-check")
        self.write("Makefile", "deployed:\n\techo one\n\nother:\n\techo b\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states(), ["run", "none"])
        self.write("Makefile", "deployed:\n\techo two\n\nother:\n\techo b\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states(), ["run", "run"])
        self.script("stamp", "deploy-check")
        self.assertEqual(self.states(), ["run", "passed"])
        run(self.repo, *commit)
        self.write("scripts/deploy.sh", "two\n")
        run(self.repo, "git", "add", "-A")
        self.assertEqual(self.states(), ["run", "run"])

    def test_the_trigger_paths(self):
        cases = {
            "scripts/task.py": ["runtime-check"],
            ".claude/hooks/guard.py": ["runtime-check"],
            ".codex/config.toml": ["runtime-check"],
            "Makefile": ["runtime-check"],
            ".github/workflows/ci.yml": ["runtime-check"],
            "scripts/deploy.sh": ["runtime-check", "deploy-check"],
            "scripts/mcp-smoke.sh": ["runtime-check", "deploy-check"],
            "compose.prod.yaml": ["runtime-check", "deploy-check"],
            "compose.yaml": ["deploy-check"],
            "app/main.py": [],
            "docs/Makefile": [],
        }
        for path, expected in cases.items():
            with self.subTest(path):
                found = [target for target in SCRIPT_SUITES if hook_selection.FOLLOWS[target](path)]
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


class HarnessSuiteTests(unittest.TestCase):
    """The harness suites that depend on the selected paths (#369)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        for name, text in {
            "tool.py": "import helper\n",
            "helper.py": "x = 1\n",
            "lonely.py": "x = 1\n",
            "other.py": "x = 1\n",
            "test_tool.py": "from tool import run\nHOOK = 'guard-cases.txt'\n",
            "test_copy.py": "for name in ('other', 'x'):\n    copy(f'{name}.py')\n",
            "test_shell.py": "SCRIPT = ROOT / 'scripts' / 'run-it.sh'\nCI = 'ci.yml'\n",
            "test_base.py": "from test_tool import Base\n",
            "test_deploy_rollback.py": "DEPLOY = 'deploy.sh'\n",
        }.items():
            (self.repo / "scripts").mkdir(exist_ok=True)
            (self.repo / "scripts" / name).write_text(text)

    def suites(self, *selected):
        found = hook_selection.harness_suites(list(selected), self.repo)
        return found if found is None else [name.removeprefix("scripts/") for name in found]

    def test_a_script_runs_the_suites_that_name_it(self):
        self.assertEqual(self.suites("scripts/tool.py"), ["test_base.py", "test_tool.py"])
        self.assertEqual(self.suites("scripts/other.py"), ["test_copy.py"])
        self.assertEqual(self.suites("scripts/run-it.sh"), ["test_shell.py"])

    def test_a_script_reached_through_another_counts(self):
        self.assertEqual(self.suites("scripts/helper.py"), ["test_base.py", "test_tool.py"])

    def test_a_suite_runs_itself_and_the_suites_that_use_it(self):
        self.assertEqual(self.suites("scripts/test_copy.py"), ["test_copy.py"])
        self.assertEqual(self.suites("scripts/test_tool.py"), ["test_base.py", "test_tool.py"])

    def test_files_outside_scripts_are_found_by_name(self):
        self.assertEqual(self.suites(".github/workflows/ci.yml"), ["test_shell.py"])
        self.assertEqual(
            self.suites(".claude/hooks/guard-cases.txt"), ["test_base.py", "test_tool.py"]
        )

    def test_several_paths_add_up_and_other_paths_do_not_count(self):
        self.assertEqual(
            self.suites("scripts/other.py", "scripts/run-it.sh", "app/main.py", "docs/x.md"),
            ["test_copy.py", "test_shell.py"],
        )
        self.assertEqual(self.suites("app/main.py"), [])

    def test_a_path_no_suite_names_runs_every_suite(self):
        for path in ("scripts/lonely.py", "scripts/new.sh", ".claude/settings.json", ".codex/zz"):
            with self.subTest(path):
                self.assertIsNone(self.suites(path, "scripts/other.py"))

    def test_what_any_suite_may_depend_on_runs_every_suite(self):
        for path in hook_selection.WHOLE_RUN:
            with self.subTest(path):
                self.assertIsNone(self.suites("scripts/other.py", path))

    def test_the_suites_runtime_check_leaves_out_are_never_chosen(self):
        self.assertEqual(self.suites("scripts/deploy.sh"), [])
        self.assertEqual(self.suites("scripts/test_deploy_rollback.py"), [])

    def test_in_this_repository_every_suite_names_what_it_runs(self):
        """A suite always runs for its own file, and a script some suite names for it."""
        root = SCRIPT.parent.parent
        every = sorted(path.name for path in SCRIPT.parent.glob("test_*.py"))
        for suite in every:
            chosen = hook_selection.harness_suites([f"scripts/{suite}"], root)
            if f"scripts/{suite}" in hook_selection.NOT_RUNTIME:
                self.assertNotIn(f"scripts/{suite}", chosen)
            else:
                self.assertIn(f"scripts/{suite}", chosen)
        for script, suite in (
            ("task_git.py", "test_task_git.py"),
            ("task_finish.py", "test_task_finish.py"),
            ("start_task.py", "test_start_task.py"),
            ("pre-commit.sh", "test_task_git.py"),
            ("docs_links.py", "test_docs_links.py"),
        ):
            with self.subTest(script):
                chosen = hook_selection.harness_suites([f"scripts/{script}"], root)
                self.assertIn(f"scripts/{suite}", chosen)
        chosen = hook_selection.harness_suites([".github/workflows/ci.yml"], root)
        self.assertIn("scripts/test_ci_workflow.py", chosen)
        chosen = hook_selection.harness_suites(["compose.prod.yaml"], root)
        self.assertIn("scripts/test_compose_prod.py", chosen)
        self.assertLess(len(hook_selection.harness_suites(["scripts/make_help.py"], root)), 5)


class CiCheckTests(unittest.TestCase):
    """Whether a pull request's paths call for the Docker job of CI (#369)."""

    def answer(self, *changed):
        return subprocess.run(  # noqa: S603
            [sys.executable, "-B", str(SCRIPT), "ci-check"],
            input="".join(path + "\n" for path in changed),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_only_a_pull_request_that_can_affect_the_job_runs_it(self):
        self.assertEqual(self.answer("scripts/task_git.py", "docs/x.md", "Makefile"), "run=false")
        self.assertEqual(self.answer(), "run=false")
        for path in (
            "app/main.py",
            "tests/test_x.py",
            "Dockerfile",
            "frontend/src/a.ts",
            "docs/progress/scope.yaml",
            "scripts/hook_selection.py",
            "scripts/docs/generate.py",
            ".github/workflows/ci.yml",
        ):
            with self.subTest(path):
                self.assertEqual(self.answer("docs/x.md", path), "run=true")


if __name__ == "__main__":
    unittest.main()
