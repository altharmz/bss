#!/usr/bin/env python3
"""The PreToolUse hook that tells make which Claude subagent runs it."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude/hooks/agent-telemetry.py"
spec = importlib.util.spec_from_file_location("agent_telemetry", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
AGENT = "a4fbc098e052c5224"


def run(payload):
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository hook
        [sys.executable, "-B", str(HOOK)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode, result.stdout


class AgentTelemetryHookTests(unittest.TestCase):
    def test_make_segments_get_the_agent_and_keep_the_make_prefix(self):
        for command, expected in (
            (
                "make start-task BSS_TASK_OWNER=claude",
                f"make BSS_TELEMETRY_AGENT={AGENT} start-task BSS_TASK_OWNER=claude",
            ),
            (
                "cd /w && make commit 2>&1 | tail -3",
                f"cd /w && make BSS_TELEMETRY_AGENT={AGENT} commit 2>&1 | tail -3",
            ),
            (
                "(make a; make b)",
                f"(make BSS_TELEMETRY_AGENT={AGENT} a; make BSS_TELEMETRY_AGENT={AGENT} b)",
            ),
        ):
            with self.subTest(command=command):
                self.assertEqual(hook.rewrite(command, AGENT), expected)

    def test_text_and_data_are_never_rewritten(self):
        for command in (
            'make commit MESSAGE="then; make test"',
            "echo make test",
            "cmake .",
            "cat > notes.md <<EOF\nmake test\nEOF",
            "git log",
            f"make BSS_TELEMETRY_AGENT={AGENT} up",
        ):
            with self.subTest(command=command):
                rewritten = hook.rewrite(command, AGENT)
                if rewritten is not None:
                    self.assertEqual(rewritten.count("BSS_TELEMETRY_AGENT"), 1)
                    self.assertIn('"then; make test"', rewritten)

    def test_only_subagent_bash_calls_are_updated(self):
        base = {"tool_name": "Bash", "tool_input": {"command": "make up", "timeout": 5}}
        self.assertEqual(run(base), (0, ""))  # main thread: no agent_id
        code, output = run({**base, "agent_id": AGENT})
        self.assertEqual(code, 0)
        updated = json.loads(output)["hookSpecificOutput"]
        self.assertEqual(updated["hookEventName"], "PreToolUse")
        self.assertEqual(
            updated["updatedInput"],
            {"command": f"make BSS_TELEMETRY_AGENT={AGENT} up", "timeout": 5},
        )
        self.assertNotIn("permissionDecision", updated)
        self.assertEqual(run({**base, "agent_id": "../../x"}), (0, ""))
        self.assertEqual(run({**base, "agent_id": AGENT, "tool_name": "Read"}), (0, ""))

    def test_malformed_input_fails_open(self):
        for payload in ("not json", "[]", '{"agent_id": 3}'):
            with self.subTest(payload=payload):
                self.assertEqual(run(payload), (0, ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
