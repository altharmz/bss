"""Real Git integration coverage in disposable task worktrees and bare remotes."""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from task_runtime import InvalidRuntime
from test_task_quality import QualityTests


class GitTests(unittest.TestCase):
    call = QualityTests.call
    write_metadata = QualityTests.write_metadata
    runtime_setup = QualityTests.runtime_setup
    quality_setup = QualityTests.setUp

    def setUp(self):
        self.quality_setup()
        shutil.copy2(
            __file__.replace("test_task_git.py", "task_git.py"), self.work / "scripts/task_git.py"
        )
        for key, value in (("user.name", "test"), ("user.email", "test@example.test")):
            self.call(["git", "config", key, value])
        for name in ("pre-commit", "pre-push"):
            (self.work / "scripts" / f"{name}.sh").write_text("exit 0\n")
        self.call(["python3", "scripts/install-hooks.py"])
        # Baseline fixture files so only each test's intended files are changed.
        self.call(["git", "add", "."])
        self.call(["git", "commit", "-m", "fixture"])
        self.remote = self.base / "origin.git"
        self.call(["git", "init", "--bare", "-q", str(self.remote)])
        self.call(["git", "remote", "add", "origin", str(self.remote)])
        self.call(["git", "push", "origin", "HEAD:refs/heads/master"])

    def command(self, operation="commit", files=None):
        self.env["MESSAGE"] = "intended change"
        self.env["FILES"] = json.dumps(files if files is not None else ["chosen.txt"])
        return self.call([shutil.which("make"), operation], check=False)

    def change(self):
        (self.work / "chosen.txt").write_text("chosen\n")

    def test_commit_only_intended_files(self):
        self.change()
        (self.work / "unrelated.txt").write_text("leave alone\n")
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.call(["git", "show", "--format=", "--name-only", "HEAD"]).stdout.strip(),
            "chosen.txt",
        )
        self.assertTrue((self.work / "unrelated.txt").exists())

    def test_clean_and_invalid_selection(self):
        for files in ([], ["missing"], ["."], [":(glob)*"]):
            self.assertNotEqual(self.command(files=files).returncode, 0)

    def test_dry_run_validates_without_staging_committing_or_running_hook(self):
        self.change()
        (self.work / "scripts/pre-commit.sh").write_text("exit 7\n")
        self.env["DRY_RUN"] = "1"
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry run validated", result.stdout)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)
        self.assertEqual(self.call(["git", "diff", "--cached", "--name-only"]).stdout, "")
        self.assertNotEqual(self.command(files=["missing"]).returncode, 0)
        self.env["GIT_NAMESPACE"] = "unexpected"
        self.assertNotEqual(self.command().returncode, 0)

    def test_dry_run_rejects_invalid_mode_and_partial_staging(self):
        self.change()
        self.env["DRY_RUN"] = "yes"
        self.assertNotEqual(self.command().returncode, 0)
        self.env["DRY_RUN"] = "1"
        self.call(["git", "add", "chosen.txt"])
        (self.work / "chosen.txt").write_text("later\n")
        self.assertNotEqual(self.command().returncode, 0)

    def test_unselected_index_and_partial_staging_preserved(self):
        self.change()
        self.call(["git", "add", "chosen.txt"])
        (self.work / "other.txt").write_text("unselected staged file must stay staged\n")
        before = self.call(["git", "diff", "--cached"]).stdout
        self.assertNotEqual(self.command(files=["other.txt"]).returncode, 0)
        self.assertEqual(self.call(["git", "diff", "--cached"]).stdout, before)
        (self.work / "chosen.txt").write_text("later edit\n")
        self.assertNotEqual(self.command().returncode, 0)
        self.assertEqual(self.call(["git", "diff", "--cached"]).stdout, before)

    def test_failed_commit_hook_leaves_index(self):
        self.change()
        (self.work / "scripts/pre-commit.sh").write_text("exit 7\n")
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        self.assertNotEqual(self.command().returncode, 0)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)
        self.assertIn("chosen.txt", self.call(["git", "diff", "--cached", "--name-only"]).stdout)

    def test_missing_hooks(self):
        (self.primary / ".git/hooks/pre-commit").unlink()
        self.change()
        self.assertNotEqual(self.command().returncode, 0)

    def test_custom_hook_configuration_rejected(self):
        self.call(["git", "config", "core.hooksPath", str(self.base / "disabled")])
        self.change()
        self.assertNotEqual(self.command().returncode, 0)

    def test_inherited_git_override_rejected(self):
        self.env["GIT_NAMESPACE"] = "unexpected"
        self.change()
        self.assertNotEqual(self.command().returncode, 0)

    def test_editor_variables_are_presentation_only_and_refusals_name_the_variable(self):
        self.change()
        self.env.update(GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true")
        self.assertEqual(self.command().returncode, 0)
        self.change()
        (self.work / "chosen.txt").write_text("second\n")
        self.env["GIT_NAMESPACE"] = "unexpected"
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GIT_NAMESPACE", result.stderr)
        self.assertNotIn("unexpected", result.stderr)

    def test_every_lifecycle_entry_point_shares_the_git_override_check(self):
        import task_git

        # The commit hook itself runs with GIT_DIR and friends: start from a clean environment.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        with patch.dict(
            os.environ, {**clean, "GIT_EDITOR": "true", "GIT_PAGER": "cat"}, clear=True
        ):
            task_git.require_no_git_overrides()
        with patch.dict(os.environ, {**clean, "GIT_DIR": "x"}, clear=True):
            with self.assertRaisesRegex(InvalidRuntime, "GIT_DIR"):
                task_git.require_no_git_overrides()
        scripts = Path(task_git.__file__).parent
        for name in ("task_pr.py", "task_finish.py", "task_git.py"):
            self.assertIn("require_no_git_overrides()", (scripts / name).read_text(), name)

    def test_message_and_files_may_come_from_files(self):
        self.change()
        message = self.base / "message.txt"
        message.write_text("from a file\n\nmentions a blocked word like --no-verify\n")
        files = self.base / "files.json"
        files.write_text(json.dumps(["chosen.txt"]))
        for key in ("MESSAGE", "FILES"):
            self.env.pop(key, None)
        self.env.update(MESSAGE_FILE=str(message), FILES_FILE=str(files))
        result = self.call([shutil.which("make"), "commit"], check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre-commit-logs", result.stdout)
        self.assertEqual(
            self.call(["git", "log", "-1", "--format=%B"]).stdout.strip(),
            "from a file\n\nmentions a blocked word like --no-verify",
        )
        for key, value in (
            ("MESSAGE_FILE", "relative.txt"),
            ("MESSAGE_FILE", str(self.base)),
            ("FILES_FILE", "relative.json"),
        ):
            with self.subTest(key=key):
                (self.work / "chosen.txt").write_text(f"{key}\n")
                previous = self.env.get(key)
                self.env[key] = value
                result = self.call([shutil.which("make"), "commit"], check=False)
                self.env[key] = previous
                self.assertNotEqual(result.returncode, 0)
        self.env["MESSAGE"] = "inline"
        result = self.call([shutil.which("make"), "commit"], check=False)
        self.assertIn("not both", result.stderr)

    def test_inherited_pager_does_not_change_commit_policy(self):
        self.env["GIT_PAGER"] = "cat"
        self.change()
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_make_command_line_assignments_do_not_leak_into_hooks(self):
        self.change()
        (self.work / "scripts/pre-commit.sh").write_text(
            "python3 -c 'import os; "
            'assert not {"MAKEFLAGS", "MAKEOVERRIDES", "MESSAGE", "FILES", "DRY_RUN"} '
            "& os.environ.keys()" + "'\n"
        )
        result = self.call(
            [shutil.which("make"), "commit", "MESSAGE=outer commit", 'FILES=["chosen.txt"]'],
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inherited_dry_run_cannot_skip_hook_validation(self):
        self.change()
        (self.work / "hook.mk").write_text("validate:\n\t@exit 7\n")
        (self.work / "scripts/pre-commit.sh").write_text(
            f"exec {shutil.which('make')} -f hook.mk validate\n"
        )
        self.env.update(MAKEFLAGS="n", FILES='["chosen.txt"]', MESSAGE="dry-run isolation")
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        result = self.call(["python3", "-B", "scripts/task_git.py", "commit"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)

    def test_deletion_and_literal_filename(self):
        name = "literal[1].txt"
        (self.work / name).write_text("literal\n")
        self.assertEqual(self.command(files=[name]).returncode, 0)
        (self.work / name).unlink()
        # Retrying after a hook failure may leave a deletion staged already.
        self.call(["git", "add", "--", name])
        self.assertEqual(self.command(files=[name]).returncode, 0)
        self.assertIn(name, self.call(["git", "show", "--format=", "--name-only", "HEAD"]).stdout)

    def test_outgoing_protected_commit_rejected(self):
        (self.work / "private.pem").touch()
        self.call(["git", "add", "private.pem"])
        self.call(["git", "commit", "-m", "unsafe historical commit"])
        (self.work / "private.pem").unlink()
        self.call(["git", "add", "private.pem"])
        self.call(["git", "commit", "-m", "remove historical file"])
        self.assertNotEqual(self.command("push").returncode, 0)

    def test_mirror_remote_rejected(self):
        self.call(["git", "config", "remote.origin.mirror", "true"])
        self.assertNotEqual(self.command("push").returncode, 0)

    def test_protected_path_and_wrong_identity(self):
        self.change()
        secret = self.work / ".env.local"
        secret.touch()
        self.assertNotEqual(self.command().returncode, 0)
        secret.unlink()
        self.metadata["task_id"] = "issue-9-wrong"
        self.write_metadata()
        self.assertNotEqual(self.command().returncode, 0)

    def test_primary_checkout_rejected(self):
        result = self.call(
            ["python3", str(self.work / "scripts/task_git.py"), "commit"],
            cwd=self.primary,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_first_and_subsequent_push_only_task_branch(self):
        self.change()
        self.assertEqual(self.command().returncode, 0)
        self.call(["git", "tag", "-a", "unrelated-tag", "-m", "fixture tag"])
        self.call(["git", "config", "push.followTags", "true"])
        for index in range(2):
            if index:
                (self.work / "chosen.txt").write_text("subsequent change\n")
                self.assertEqual(self.command().returncode, 0)
            result = self.command("push")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.call(["git", "rev-parse", "--abbrev-ref", "@{upstream}"]).stdout.strip(),
            f"origin/{self.task}",
        )
        self.assertEqual(self.call(["git", "ls-remote", "--tags", "origin"]).stdout, "")

    def test_non_fast_forward_and_failed_push_hook(self):
        self.change()
        self.assertEqual(self.command().returncode, 0)
        self.assertEqual(self.command("push").returncode, 0)
        self.call(["git", "clone", "-q", str(self.remote), str(self.base / "other")])
        other = self.base / "other"
        self.call(["git", "checkout", self.task], cwd=other)
        self.call(
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.test",
                "commit",
                "--allow-empty",
                "-m",
                "remote advance",
            ],
            cwd=other,
        )
        self.call(["git", "push", "origin", self.task], cwd=other)
        self.assertNotEqual(self.command("push").returncode, 0)

    def test_push_hook_rejection(self):
        (self.work / "scripts/pre-push.sh").write_text("exit 7\n")
        self.assertNotEqual(self.command("push").returncode, 0)
        self.assertEqual(
            self.call(["git", "ls-remote", "origin", f"refs/heads/{self.task}"]).stdout, ""
        )

    def test_unexpected_remote_and_upstream(self):
        self.call(["git", "config", f"branch.{self.task}.merge", "refs/heads/master"])
        self.assertNotEqual(self.command("push").returncode, 0)
        self.call(["git", "config", "remote.origin.pushurl", str(self.base / "other.git")])
        self.assertNotEqual(self.command("push").returncode, 0)

    def test_telemetry_unavailable(self):
        (self.bin / "docker").write_text("#!/bin/sh\nexit 1\n")
        self.change()
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("event not recorded", result.stderr)

    def advance_master(self, filename="upstream.txt", content="upstream\n"):
        other = self.base / "upstream"
        self.call(["git", "clone", "-q", str(self.remote), str(other)])
        (other / filename).write_text(content)
        self.call(["git", "add", filename], cwd=other)
        self.call(
            ["git", "-c", "user.name=test", "-c", "user.email=t@t", "commit", "-qm", "upstream"],
            cwd=other,
        )
        self.call(["git", "push", "origin", "master"], cwd=other)
        return self.call(["git", "rev-parse", "HEAD"], cwd=other).stdout.strip()

    def test_sync_preserves_published_history_and_runs_commit_hook(self):
        self.change()
        self.assertEqual(self.command().returncode, 0)
        self.assertEqual(self.command("push").returncode, 0)
        previous = self.call(["git", "rev-parse", "HEAD"]).stdout.strip()
        upstream = self.advance_master()
        refused = self.command("push")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("make sync-task", refused.stderr)
        result = self.command("sync-task")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.call(["git", "merge-base", "--is-ancestor", previous, "HEAD"])
        self.call(["git", "merge-base", "--is-ancestor", upstream, "HEAD"])
        self.assertEqual(self.command("push").returncode, 0)
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        self.assertEqual(self.command("sync-task").returncode, 0)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)

    def test_sync_hook_failure_preserves_merge_and_index(self):
        flag = self.base / "fail-hook"
        (self.work / "scripts/pre-commit.sh").write_text(f"test ! -f '{flag}'\n")
        self.assertEqual(self.command(files=["scripts/pre-commit.sh"]).returncode, 0)
        self.advance_master()
        flag.touch()
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        self.assertNotEqual(self.command("sync-task").returncode, 0)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)
        self.call(["git", "rev-parse", "--verify", "MERGE_HEAD"])
        self.assertIn("upstream.txt", self.call(["git", "diff", "--cached", "--name-only"]).stdout)

    def test_sync_conflict_preserves_both_histories(self):
        self.change()
        self.assertEqual(self.command().returncode, 0)
        self.advance_master("chosen.txt", "different upstream\n")
        head = self.call(["git", "rev-parse", "HEAD"]).stdout
        self.assertNotEqual(self.command("sync-task").returncode, 0)
        self.assertEqual(self.call(["git", "rev-parse", "HEAD"]).stdout, head)
        self.assertIn(
            "chosen.txt", self.call(["git", "diff", "--name-only", "--diff-filter=U"]).stdout
        )
        (self.work / "chosen.txt").write_text("explicit source resolution\n")
        result = self.command(files=["chosen.txt"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call(["git", "diff", "--name-only", "--diff-filter=U"]).stdout, "")

    def test_sync_dirty_and_remote_mismatch_refused(self):
        self.change()
        result = self.command("sync-task")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commit intended changes", result.stderr)
        self.assertEqual(self.command().returncode, 0)
        self.call(["git", "config", "remote.origin.pushurl", str(self.base / "unexpected.git")])
        self.assertNotEqual(self.command("sync-task").returncode, 0)


del QualityTests

if __name__ == "__main__":
    unittest.main()
