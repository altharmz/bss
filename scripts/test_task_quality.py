"""Disposable repository integration coverage of the public quality targets."""

import json
import shutil
import unittest

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
        child_make.write_text(
            "#!/bin/sh\n"
            'echo "ran-$1"\n'
            '[ "$1" != "$BSS_TEST_FAIL_TARGET" ]\n'
        )
        child_make.chmod(0o755)
        (self.work / ".gitignore").write_text(".task/\n.env*\n*.key\n")

    def quality(self, target="review-task", **kwargs):
        return self.call([shutil.which("make"), target], check=False, **kwargs)

    def test_public_targets(self):
        for target in ("review-task", "format", "check"):
            with self.subTest(target=target):
                result = self.quality(target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(self.task, result.stdout)
                self.assertNotIn("fixture-secret-never-output", result.stdout + result.stderr)

    def test_failed_child(self):
        for target, child in (("format", "fmt"), ("check", "check-local")):
            self.env["BSS_TEST_FAIL_TARGET"] = child
            self.assertNotEqual(self.quality(target).returncode, 0)

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
