#!/usr/bin/env python3
"""The agents' shell guard: every case file, through the hook as the clients call it."""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude/hooks/guard.py"
spec = importlib.util.spec_from_file_location("guard", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def cases(path):
    """(expected, command) pairs; a literal \\n in a case is a newline."""
    for line in (ROOT / path).read_text().splitlines():
        if line and not line.startswith("#"):
            expected, command = line.split("\t", 1)
            yield expected, command.replace("\\n", "\n")


def run(stdin, *flags):
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository hook
        [sys.executable, "-B", str(HOOK), *flags],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode, result.stderr


def patch_cases():
    """(expected, operation, path, cwd); $CONFIG in a path is the configuration directory."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    for line in (ROOT / ".codex/patch-guard-cases.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            expected, operation, path, cwd = line.split("\t")
            yield expected, operation, path.replace("$CONFIG", config), cwd


class PatchGuardTests(unittest.TestCase):
    """.codex/patch-guard.sh: a one-file patch per case, as Codex sends it."""

    def run_guard(self, payload):
        return subprocess.run(  # noqa: S603 - fixed shell and repository hook
            ["bash", str(ROOT / ".codex/patch-guard.sh")],  # noqa: S607
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_cases(self):
        seen = set()
        for expected, operation, path, cwd in patch_cases():
            patch = f"*** Begin Patch\n*** {operation}: {path}\n*** End Patch\n"
            payload = {"cwd": str(ROOT / cwd), "tool_input": {"command": patch}}
            with self.subTest(operation=operation, path=path, cwd=cwd):
                self.assertIn(expected, ("allow", "block"))
                result = self.run_guard(json.dumps(payload))
                self.assertEqual("block" if result.returncode == 2 else "allow", expected)
                self.assertEqual(bool(result.stderr), expected == "block", result.stderr)
            seen.add(expected)
        self.assertEqual(seen, {"allow", "block"})

    def test_an_unreadable_payload_is_blocked(self):
        result = self.run_guard("not json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read the apply_patch payload", result.stderr)


class GuardTests(unittest.TestCase):
    def check(self, path, codex):
        seen = set()
        for expected, command in cases(path):
            with self.subTest(command=command):
                self.assertIn(expected, ("allow", "block"))
                reason = guard.decide(command, codex=codex)
                self.assertEqual("block" if reason else "allow", expected, reason)
            seen.add(expected)
        self.assertEqual(seen, {"allow", "block"})

    def test_shared_cases(self):
        self.check(".claude/hooks/guard-cases.txt", codex=False)

    def test_shared_cases_hold_for_codex_unless_they_are_bare_git_writes(self):
        for expected, command in cases(".claude/hooks/guard-cases.txt"):
            if expected == "allow" and guard.CODEX_GIT.search(guard.executable(command)):
                expected = "block"
            with self.subTest(command=command):
                reason = guard.decide(command, codex=True)
                self.assertEqual("block" if reason else "allow", expected, reason)

    def test_codex_cases(self):
        self.check(".codex/guard-cases.txt", codex=True)

    def test_the_codex_rule_is_codex_only(self):
        for command in ("git add -A", "git commit -m x", "git push origin HEAD"):
            self.assertIsNone(guard.decide(command))
            self.assertIn("make commit", guard.decide(command, codex=True))

    def test_hook_blocks_with_exit_2_and_a_reason(self):
        payload = json.dumps({"tool_input": {"command": "cat .env"}})
        self.assertEqual(run(payload)[0], 2)
        self.assertIn("BLOCKED by guard.py: do not read .env files", run(payload)[1])
        self.assertEqual(run(json.dumps({"tool_input": {"command": "make test"}})), (0, ""))
        commit = json.dumps({"tool_input": {"command": "git commit -m x"}})
        self.assertEqual(run(commit), (0, ""))
        self.assertEqual(run(commit, "--codex")[0], 2)

    def test_unreadable_payload_passes_for_claude_and_blocks_for_codex(self):
        for payload in ("", "not json", "[]", json.dumps({"tool_input": {"command": 1}})):
            with self.subTest(payload=payload):
                self.assertEqual(run(payload), (0, ""))
                self.assertEqual(run(payload, "--codex")[0], 2)
        self.assertEqual(run(json.dumps({"tool_input": {}}), "--codex"), (0, ""))


if __name__ == "__main__":
    unittest.main()
