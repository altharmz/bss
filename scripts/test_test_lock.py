#!/usr/bin/env python3
"""Makefile: every heavy test run goes through scripts/with-test-lock (#348).

The wrapper's behaviour has its cases in scripts/test-pre-commit-lock.sh; this suite keeps
a new heavy recipe line from being added without it, and the light targets free of it.
"""

import re
import unittest
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"
LOCKED = "$(LOCKED)"
# What makes a recipe line heavy: the backend suite, the host script suites, the UI tests,
# the browser tests and the progress collector (it reruns the backend suite).
HEAVY = re.compile(
    r"\bpytest\b|scripts/run_suites\.py|npm run test|\bvitest\b|playwright"
    r"|task-compose run --rm e2e\b|tests\.progress_collect"
)
MUST_LOCK = {
    "coverage",
    "test",
    "test-smoke",
    "test-changed",
    "test-recorded",
    "test-files",
    "check-preflight",
    "progress",
    "runtime-check",
    "deploy-check",
    "web-test-once",
    "web-test-timezone",
    "web-e2e",
}
NEVER_LOCK = {
    "lint",
    "fmt",
    "format",
    "web-lint",
    "web-fmt",
    "web-build",
    "web-types",
    "docs",
    "docs-check",
    "docs-links",
    "scripts-lint",
    "scripts-fmt",
    "up",
    "migrate",
    "seed",
    "check",
    "web-test",
    "review-task",
    "commit",
    "create-pr",
    "check-pr",
    "merge-task",
    "start-task",
    "finish-task",
    "telemetry-collect",
}
RULE = re.compile(r"^([A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)*)\s*:(?![=])")


def recipes(text=None):
    """{target: [recipe line, ...]}, continuation lines joined."""
    text = MAKEFILE.read_text() if text is None else text
    result, current, pending = {}, None, ""
    for line in text.splitlines():
        if line.startswith("\t") and current is not None:
            pending += line.strip()
            if pending.endswith("\\"):
                pending = pending[:-1].rstrip() + " "
                continue
            result.setdefault(current, []).append(pending)
            pending = ""
            continue
        match = RULE.match(line)
        if match and not line.startswith((".PHONY", "#")):
            names = match.group(1).split()
            # A rule naming several targets (a shared prerequisite) owns no recipe here.
            current = names[0] if len(names) == 1 else None
            if current:
                result.setdefault(current, [])
        elif line.strip() and not line.startswith("\t"):
            current = None
    return result


class MakefileTestLockTests(unittest.TestCase):
    def test_locked_is_the_wrapper(self):
        self.assertRegex(MAKEFILE.read_text(), r"(?m)^LOCKED := \./scripts/with-test-lock$")
        self.assertTrue((MAKEFILE.parent / "scripts/with-test-lock").stat().st_mode & 0o111)

    def test_every_heavy_recipe_line_takes_the_lock(self):
        unlocked = [
            f"{target}: {line}"
            for target, lines in recipes().items()
            for line in lines
            if HEAVY.search(line) and not line.lstrip("@-+").startswith(LOCKED)
        ]
        self.assertEqual(unlocked, [], "prefix these heavy recipe lines with $(LOCKED)")

    def test_the_listed_heavy_targets_lock(self):
        found = recipes()
        missing = sorted(
            target
            for target in MUST_LOCK
            # make's prefixes (-: carry on after a failure) as in the line check above
            if not any(line.lstrip("@-+").startswith(LOCKED) for line in found.get(target, []))
        )
        self.assertEqual(missing, [])

    def test_light_targets_never_lock(self):
        found = recipes()
        self.assertTrue(NEVER_LOCK <= found.keys(), sorted(NEVER_LOCK - found.keys()))
        locked = sorted(
            target for target in NEVER_LOCK if any(LOCKED in line for line in found[target])
        )
        self.assertEqual(locked, [])

    def test_the_check_sees_an_unlocked_heavy_line(self):
        # The parser itself: a heavy line without the wrapper is reported, continuation and all.
        sample = "x: ## doc\n\t./scripts/task-compose run --rm \\\n\t\toneoff pytest -n auto\n"
        line = "./scripts/task-compose run --rm oneoff pytest -n auto"
        self.assertEqual(recipes(sample), {"x": [line]})
        self.assertRegex(line, HEAVY)


if __name__ == "__main__":
    unittest.main()
