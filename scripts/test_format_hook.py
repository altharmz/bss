#!/usr/bin/env python3
"""The PostToolUse format hook: which Compose service formats which edited file."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude/hooks/format.sh"
# Stands in for scripts/task-compose: records its arguments instead of starting a container.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "${*//$'\\n'/ }" >>"$(dirname "$0")/../calls"
exit "${STUB_EXIT:-0}"
"""


class FormatHookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name).resolve()
        (self.project / "scripts").mkdir()
        stub = self.project / "scripts/task-compose"
        stub.write_text(STUB)
        stub.chmod(0o755)

    def edit(self, name, env=None):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
        result = subprocess.run(  # noqa: S603 - fixed shell and repository hook
            ["bash", str(HOOK)],  # noqa: S607
            input=json.dumps({"tool_input": {"file_path": str(path)}}),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(self.project), **(env or {})},
        )
        calls = self.project / "calls"
        called = calls.read_text().splitlines() if calls.exists() else []
        calls.unlink(missing_ok=True)
        return result, called

    def test_application_python_is_formatted_in_the_tools_service(self):
        for name in ("app/main.py", "bss_mcp/server.py", "tests/test_a.py", "loadtest/seed.py"):
            with self.subTest(name=name):
                result, called = self.edit(name)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(len(called), 1)
                self.assertIn(f"-e f={name} tools sh -c", called[0])

    def test_harness_scripts_are_formatted_in_their_own_service(self):
        for name in ("scripts/task_git.py", "scripts/docs/generate.py"):
            with self.subTest(name=name):
                result, called = self.edit(name)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(len(called), 1)
                self.assertIn(f"-e f={name} scripts-tools-fmt sh -c", called[0])

    def test_a_file_no_service_mounts_passes_silently(self):
        for name in (".claude/hooks/agent-telemetry.py", "ops/tool.py", "docs/notes.md"):
            with self.subTest(name=name):
                result, called = self.edit(name)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertEqual(called, [])

    def test_a_formatter_that_cannot_run_is_reported(self):
        result, called = self.edit("scripts/task_git.py", env={"STUB_EXIT": "125"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("format hook FAILED to run", result.stderr)
        self.assertEqual(len(called), 1)


if __name__ == "__main__":
    unittest.main()
