"""Disposable repository integration coverage of the public quality targets."""

import json
import shutil
import unittest
from unittest.mock import patch

from task_quality import changed_paths, check_stages, format_targets, run_stages, web_test
from test_task_runtime import SCRIPTS, RuntimeTests


class QualityTests(unittest.TestCase):
    call = RuntimeTests.call
    write_metadata = RuntimeTests.write_metadata
    runtime_setup = RuntimeTests.setUp

    def setUp(self):
        self.runtime_setup()
        shutil.copy2(SCRIPTS / "task_quality.py", self.work / "scripts/task_quality.py")
        shutil.copy2(SCRIPTS.parent / "Makefile", self.work / "Makefile")
        child_make = self.bin / "make"
        child_make.write_text('#!/bin/sh\necho "ran-$1"\n[ "$1" != "$BSS_TEST_FAIL_TARGET" ]\n')
        child_make.chmod(0o755)
        (self.work / ".gitignore").write_text(".task/\n.env*\n*.key\n")

    def quality(self, target="review-task", **kwargs):
        return self.call([shutil.which("make"), target], check=False, **kwargs)

    def test_public_targets(self):
        for target in ("review-task", "format", "check"):
            with self.subTest(target=target):
                result = self.quality(target)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Context lines: always for review-task, otherwise only with VERBOSE=1.
                self.assertEqual(f'"task": "{self.task}"' in result.stdout, target == "review-task")
                self.assertEqual('{"staged"' in result.stdout, target == "review-task")
                self.assertNotIn("fixture-secret-never-output", result.stdout + result.stderr)

    def test_verbose_prints_the_full_context_before_the_result(self):
        self.env["VERBOSE"] = "1"
        result = self.quality("format")
        self.assertEqual(result.returncode, 0, result.stderr)
        for key in (f'"task": "{self.task}"', '{"modified"', '{"staged"', '{"untracked"'):
            self.assertIn(key, result.stdout)

    def test_failed_child(self):
        for target, child in (("format", "fmt"), ("check", "check-preflight")):
            self.env["BSS_TEST_FAIL_TARGET"] = child
            self.assertNotEqual(self.quality(target).returncode, 0)

    def test_check_reports_all_failures_and_continues(self):
        self.env["BSS_TEST_FAIL_TARGET"] = "check-preflight"
        result = self.quality("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ran-guard-check", result.stdout)
        self.assertIn("failed: check-preflight", result.stdout)
        self.assertLess(result.stdout.index("ran-check-preflight"), result.stdout.index("ran-test"))

    def test_stage_selection_is_conservative(self):
        for names in (None, set(), {"app/api/example.py"}, {"Makefile"}, {"unknown"}):
            self.assertIn("web-e2e", check_stages(names))
        for names in ({"scripts/task_git.py"}, {"docs/worktrees.md", ".claude/hooks/guard.sh"}):
            stages = check_stages(names)
            self.assertNotIn("web-test", stages)
            self.assertNotIn("web-e2e", stages)
            self.assertEqual(stages[0], "check-preflight")
            self.assertIn("test", stages)

    def test_format_skips_the_ui_formatter_without_ui_changes(self):
        for names in (None, {"frontend/src/App.tsx"}, {"spec/tmf.json", "scripts/a.py"}):
            self.assertEqual(format_targets(names), ["fmt", "web-fmt"])
        for names in (set(), {"scripts/task_git.py"}, {"app/main.py", "docs/x.md"}):
            with patch("builtins.print") as shown:
                self.assertEqual(format_targets(names), ["fmt"])
            self.assertIn("skipping web-fmt", shown.call_args.args[0])

    def test_multiple_failures_are_reported(self):
        from types import SimpleNamespace

        with (
            patch(
                "task_quality.subprocess.run",
                side_effect=[
                    SimpleNamespace(returncode=1),
                    SimpleNamespace(returncode=0),
                    SimpleNamespace(returncode=2),
                ],
            ) as run,
            patch("builtins.print") as output,
        ):
            self.assertEqual(run_stages(self.work, ["first", "second", "last"]), 1)
            self.assertEqual(run.call_count, 3)
            output.assert_called_with("task quality: failed: first, last")

    def test_preflight_prepares_task_schema_before_testing(self):
        result = self.call([shutil.which("make"), "-n", "check-preflight"])
        self.assertLess(
            result.stdout.index("alembic upgrade head"), result.stdout.index("pytest -q")
        )
        self.assertLess(result.stdout.index("mkdir -p .testmon"), result.stdout.index("pytest -q"))

    def test_testmon_directory_preparation_preserves_data(self):
        directory = self.work / ".testmon"
        directory.mkdir()
        directory.chmod(0o555)
        try:
            self.call([shutil.which("make"), "testmon-dir"])
            data = directory / "existing-data"
            # Root can write despite mode 0555; regular development users recreate
            # the empty directory. Both must preserve any existing cache data.
            directory.chmod(0o755)
            data.write_text("keep")
            self.call([shutil.which("make"), "testmon-dir"])
            self.assertEqual(data.read_text(), "keep")
        finally:
            directory.chmod(0o755)

    def test_staged_changes_count_even_when_worktree_matches_base(self):
        self.call(["git", "add", "."])
        self.call(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "fixture"])
        self.call(["git", "update-ref", "refs/remotes/origin/master", "HEAD"])
        file = self.work / "frontend/src/example.ts"
        file.parent.mkdir(parents=True)
        file.write_text("staged\n")
        self.call(["git", "add", "frontend/src/example.ts"])
        file.unlink()
        # Like fixture subprocesses, in-process Git queries must not inherit the
        # real committing worktree's GIT_DIR when this suite runs inside a hook.
        with patch.dict("os.environ", self.env, clear=True):
            self.assertIn("frontend/src/example.ts", changed_paths(self.work))

    def test_changed_paths_include_branch_index_worktree_and_new_files(self):
        self.call(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "--allow-empty",
                "-m",
                "base",
            ]
        )
        self.call(["git", "update-ref", "refs/remotes/origin/master", "HEAD"])
        file = self.work / "scripts/change.py"
        file.write_text("first\n")
        self.call(["git", "add", "scripts/change.py"])
        self.call(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "change"])
        result = self.quality("check")
        # Untracked Makefile and helper files still count as unknown inputs.
        self.assertIn("ran-web-test", result.stdout)
        self.call(["git", "add", "."])
        self.call(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "fixture"])
        self.call(["git", "update-ref", "refs/remotes/origin/master", "HEAD"])
        file.write_text("second\n")
        self.assertNotIn("ran-web-test", self.quality("check").stdout)
        self.call(["git", "add", "scripts/change.py"])
        self.assertNotIn("ran-web-test", self.quality("check").stdout)
        self.call(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "script"])
        self.assertNotIn("ran-web-test", self.quality("check").stdout)
        (self.work / "new-input").touch()
        self.assertIn("ran-web-test", self.quality("check").stdout)

    def test_timezone_selection(self):
        for names, ci, second in (
            ({"scripts/helper.py"}, "", False),
            ({"frontend/src/date.ts"}, "", True),
            ({"frontend/package.json"}, "true", True),
            (None, "", True),
        ):
            with (
                patch("task_quality.changed_paths", return_value=names),
                patch.dict("os.environ", {"CI": ci}),
                patch("task_quality.run_stages", return_value=0) as run,
            ):
                self.assertEqual(web_test(self.work), 0)
                self.assertEqual("web-test-timezone" in run.call_args.args[1], second)

    def test_primary_dirty(self):
        (self.primary / "unexpected").touch()
        self.assertNotEqual(self.quality().returncode, 0)

    def test_secret_names(self):
        for name in (".env.local", "private.key"):
            path = self.work / name
            path.write_text("never-print-this-secret")
            result = self.quality()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("never-print-this-secret", result.stdout + result.stderr)
            path.unlink()

    def test_forbidden_untracked(self):
        (self.work / "backup.pem").touch()
        self.assertNotEqual(self.quality().returncode, 0)

    def test_force_added_generated_artifact(self):
        with (self.work / ".gitignore").open("a") as output:
            output.write("generated/\n")
        generated = self.work / "generated"
        generated.mkdir()
        (generated / "asset.js").touch()
        self.call(["git", "add", "-f", "generated/asset.js"])
        result = self.quality()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ignored/generated artifact", result.stderr)

    def test_slot_mismatch(self):
        (self.work.parent / "active.json").write_text('{"version":1,"task_id":"wrong"}')
        self.assertNotEqual(self.quality().returncode, 0)

    def test_valid_slot_and_mismatched_reservation(self):
        slot = self.base / "worker1"
        slot.mkdir()
        destination = slot / "repo"
        self.call(["git", "worktree", "move", str(self.work), str(destination)])
        self.work = destination
        active = slot / "active.json"
        active.write_text(json.dumps({"version": 1, "task_id": self.task}))
        result = self.quality()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"slot": "worker1"', result.stdout)
        active.write_text(json.dumps({"version": 1, "task_id": "issue-9-wrong"}))
        self.assertNotEqual(self.quality().returncode, 0)

    def test_telemetry_unavailable_is_fail_open(self):
        (self.bin / "docker").write_text("#!/bin/sh\necho sensitive-value >&2\nexit 1\n")
        result = self.quality()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("telemetry: event not recorded", result.stderr)
        self.assertNotIn("sensitive-value", result.stdout + result.stderr)

    def test_branch_metadata_mismatch_and_symlink(self):
        self.metadata["task_id"] = "issue-9-wrong"
        self.write_metadata()
        self.assertNotEqual(self.quality().returncode, 0)
        metadata = self.work / ".task/runtime.json"
        metadata.unlink()
        metadata.symlink_to(self.secret)
        result = self.quality()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("fixture-secret-never-output", result.stdout + result.stderr)

    def test_diff_check_staged_and_unstaged(self):
        file = self.work / "example"
        file.write_text("trailing whitespace  \n")
        self.call(["git", "add", "example"])
        self.assertNotEqual(self.quality().returncode, 0)
        self.assertEqual(self.quality("format").returncode, 0)
        file.write_text("clean\n")
        self.call(["git", "add", "example"])
        file.write_text("trailing again  \n")
        self.assertNotEqual(self.quality().returncode, 0)

    def test_wrong_checkout(self):
        shutil.copytree(self.work / "scripts", self.primary / "scripts")
        result = self.call(
            ["python3", "scripts/task_quality.py", "format"], cwd=self.primary, check=False
        )
        self.assertNotEqual(result.returncode, 0)


del RuntimeTests

if __name__ == "__main__":
    unittest.main()
