"""Public Make source selection, confined mounts and accurate lint failures."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import script_quality as quality


class ScriptQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/example.py").write_text("value = 1\n")

    def test_changed_selection_ignores_other_sources_and_deleted_files(self):
        with patch.object(
            quality,
            "changed_paths",
            return_value={
                "scripts/example.py",
                "scripts/deleted.py",
                "app/main.py",
                "docs/example.md",
            },
        ):
            self.assertEqual(quality.select(self.root), ["/app/scripts/example.py"])

    def test_explicit_paths_are_validated_before_any_tool_runs(self):
        for names in (
            ["scripts/../secret.py"],
            ["scripts/example.py;touch-x"],
            ["app/main.py"],
            ["scripts/missing.py"],
            [],
            "scripts/example.py",
            [None],
        ):
            with self.subTest(names=names), self.assertRaises(ValueError):
                quality.select(self.root, json.dumps(names))
        linked = self.root / "scripts/linked.py"
        linked.symlink_to(self.root / "scripts/example.py")
        with self.assertRaises(ValueError):
            quality.select(self.root, '["scripts/linked.py"]')

    def test_readonly_lint_and_scoped_writable_formatter_propagate_failures(self):
        for operation in ("lint", "format"):
            with patch.object(
                quality.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=2),
                    *([SimpleNamespace(returncode=2, stdout="[]")] if operation == "lint" else []),
                    SimpleNamespace(returncode=0),
                ],
            ) as run:
                self.assertEqual(quality.run(self.root, operation, ["/app/scripts/example.py"]), 2)
                self.assertEqual(run.call_count, 3 if operation == "lint" else 2)
                args = run.call_args.args[0]
                self.assertIn(
                    "scripts-tools-fmt" if operation == "format" else "scripts-tools", args
                )
                self.assertNotIn("shell", run.call_args.kwargs)
                self.assertNotIn("-v", args)

    def test_noqa_on_a_neighbouring_line_gets_a_hint_naming_the_right_line(self):
        (self.root / "scripts/example.py").write_text(
            "call(  # noqa: S603\n    argument,\n)\nother()  # noqa: S607\n"
        )
        findings = [
            {"code": "S603", "filename": "/app/scripts/example.py", "location": {"row": 2}},
            {"code": "S607", "filename": "/app/scripts/example.py", "location": {"row": 4}},
            {"code": "E501", "filename": "/app/scripts/example.py", "location": {"row": 2}},
        ]
        hints = quality.noqa_hints(self.root, findings)
        self.assertEqual(len(hints), 1)
        self.assertIn("scripts/example.py:2 reports S603", hints[0])
        self.assertIn("on line 1", hints[0])
        self.assertIn("move it to line 2", hints[0])

    def test_failed_lint_prints_the_hint_once_after_the_ruff_output(self):
        (self.root / "scripts/example.py").write_text("x(  # noqa: S603\n    y)\n")
        report = json.dumps(
            [{"code": "S603", "filename": "/app/scripts/example.py", "location": {"row": 2}}]
        )
        with (
            patch.object(
                quality.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=1),
                    SimpleNamespace(returncode=1, stdout=report),
                    SimpleNamespace(returncode=0),
                ],
            ) as run,
            patch("sys.stderr") as err,
        ):
            self.assertEqual(quality.run(self.root, "lint", ["/app/scripts/example.py"]), 1)
        self.assertEqual(run.call_count, 3)
        self.assertIn("json", run.call_args_list[1].args[0])
        self.assertIn("line 1", "".join(c.args[0] for c in err.write.call_args_list))

    def test_public_make_targets_forward_selection_without_shell_evaluation(self):
        executable = self.root / "python3"
        executable.write_text(
            f"#!{sys.executable}\nimport json,os,sys\n"
            "print(json.dumps([sys.argv[1:],os.environ.get('BSS_SCRIPT_FILES')]))\n"
        )
        executable.chmod(0o700)
        marker = self.root / "must-not-exist"
        selection = json.dumps([f"scripts/`touch {marker}`.py"])
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "BSS_SCRIPT_FILES"}
        }
        env["PATH"] = str(self.root) + os.pathsep + env["PATH"]
        for target, action in (("scripts-lint", "lint"), ("scripts-fmt", "format")):
            result = subprocess.run(  # noqa: S603, S607 - fixed public target, disposable executable
                ["make", "--no-print-directory", target, "SCRIPT_FILES=" + selection],  # noqa: S607
                cwd=Path(__file__).resolve().parent.parent,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            observed = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(observed, [["-B", "scripts/script_quality.py", action], selection])
            self.assertFalse(marker.exists())

        # Make expressions must reach validation as literal data too, rather than
        # executing while Make constructs the child's environment.
        selection = json.dumps([f"scripts/$(shell touch {marker}).py"])
        for target in ("scripts-lint", "scripts-fmt"):
            result = subprocess.run(  # noqa: S603 - fixed Make target and test-owned executable
                ["make", "--no-print-directory", target, "SCRIPT_FILES=" + selection],  # noqa: S607
                cwd=Path(__file__).resolve().parent.parent,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout.splitlines()[-1])[1], selection)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
