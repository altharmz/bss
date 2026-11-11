"""Generic lifecycle failures name their cause and leak nothing."""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import start_task
import task_finish
import task_git
import task_pr
import task_quality
from task_runtime import diagnostic

SECRET = "ghp_" + "a" * 30
LEAK = f"token {SECRET} https://user:pw@example.com/path private prompt text"


def failure(cmd, code=3, stderr=LEAK):
    return subprocess.CalledProcessError(code, cmd, output=LEAK, stderr=stderr)


class DiagnosticTests(unittest.TestCase):
    def test_names_class_first_two_arguments_and_exit_status_only(self):
        text = diagnostic(failure(["git", "commit", "-m", LEAK]))
        self.assertEqual(text, "CalledProcessError; command git commit; exit 3")

    def test_git_dash_c_names_the_subcommand_not_the_directory(self):
        text = diagnostic(failure(["git", "-C", "/home/user/private-dir", "status", "--x"]))
        self.assertEqual(text, "CalledProcessError; command git status; exit 3")

    def test_gh_detail_is_sanitized(self):
        text = diagnostic(failure(["gh", "api", "graphql"], stderr=LEAK))
        self.assertTrue(text.startswith("CalledProcessError; command gh api; exit 3; "))
        self.assertNotIn(SECRET, text)
        self.assertNotIn("user:pw", text)
        self.assertLessEqual(len(text), 200)

    def test_other_exceptions_name_only_the_class_and_errno(self):
        self.assertEqual(diagnostic(KeyError(LEAK)), "KeyError")
        self.assertEqual(diagnostic(ValueError(LEAK)), "ValueError")
        self.assertEqual(diagnostic(FileNotFoundError(2, LEAK)), "FileNotFoundError; ENOENT")
        self.assertEqual(
            diagnostic(subprocess.TimeoutExpired(["gh", "pr", LEAK], 5)),
            "TimeoutExpired; command gh pr; timed out",
        )

    def test_long_or_url_arguments_are_bounded(self):
        text = diagnostic(failure(["x" * 500, "https://user:pw@example.com/a"]))
        self.assertNotIn("user:pw", text)
        self.assertLess(len(text), 100)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        # A git hook exports GIT_* selectors, which the entry points refuse.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        patcher = patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, module, argv, target, error, code=1):
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["x", *argv]),
            patch.object(module, target, side_effect=error),
            patch.object(module, "record", create=True),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(module.main(), code)
        return stderr.getvalue()

    def check(self, text, *causes):
        for cause in causes:
            self.assertIn(cause, text)
        for secret in (SECRET, "user:pw", "private prompt text"):
            self.assertNotIn(secret, text)

    def test_commit_names_the_failing_command(self):
        text = self.run_main(
            task_git, ["push"], "context", failure(["git", "push", "origin", LEAK])
        )
        self.check(text, "Git/hook failed or refused", "command git push", "exit 3")

    def test_pr_names_the_failing_command(self):
        # gh's first stderr line is shown through the shared sanitizer; credentials and
        # URLs in it are not.
        error = failure(["gh", "pr", "view", LEAK], stderr=f"token {SECRET} https://user:pw@x.io")
        text = self.run_main(task_pr, ["check-pr"], "context", error)
        self.check(text, "command gh pr", "exit 3", "<redacted>")

    def test_finish_names_the_failing_command(self):
        with patch.object(
            task_finish.task_repo, "primary", side_effect=failure(["docker", "volume", LEAK])
        ):
            text = self.run_main(
                task_finish, ["finish-task"], "slot_path", failure(["docker", "compose", LEAK])
            )
        self.check(text, "operation failed", "command docker", "exit 3")

    def test_quality_names_a_non_runtime_failure(self):
        text = self.run_main(task_quality, ["format"], "context", failure(["git", "diff", LEAK]))
        self.check(text, "command git diff", "exit 3")

    def test_startup_names_an_unexpected_exception_class(self):
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["start-task"]),
            patch.object(start_task, "start", side_effect=KeyError(LEAK)),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(start_task.main(), 1)
        self.check(stderr.getvalue(), "start-task: KeyError")


if __name__ == "__main__":
    unittest.main()
