#!/usr/bin/env python3
""".github/workflows/ci.yml: what starts a run, and what a run may write (stdlib only).

The workflow is read by indentation, which is all its plain block style needs: no run on a
push to `master` (`make merge-task` merges only a green pull request that is not behind
`master`, so that run tested the same code again, #366), and one job that may write
anything at all: the report of a failed nightly run, which opens an issue.
"""

import re
import unittest
from pathlib import Path

import hook_selection

LINES = (
    (Path(__file__).resolve().parent.parent / ".github/workflows/ci.yml").read_text().split("\n")
)


def indent(line):
    return len(line) - len(line.lstrip())


def block(lines, key):
    """The lines under `key:` in a block: everything indented deeper than the key."""
    level = min(
        indent(line) for line in lines if line.strip() and not line.lstrip().startswith("#")
    )
    for number, line in enumerate(lines):
        if indent(line) == level and re.match(rf"{re.escape(key)}:\s*(#.*)?$", line.strip()):
            body = []
            for following in lines[number + 1 :]:
                if following.strip() and indent(following) <= level:
                    break
                body.append(following)
            return body
    raise KeyError(key)


def keys(lines):
    """The keys of a block, comments left out."""
    level = min(
        indent(line) for line in lines if line.strip() and not line.lstrip().startswith("#")
    )
    return [
        line.split(":", 1)[0].strip()
        for line in lines
        if line.strip() and indent(line) == level and not line.lstrip().startswith("#")
    ]


def value(lines, key):
    """The scalar after `key:` in a block."""
    for line in lines:
        if line.strip().startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    raise KeyError(key)


def entries(lines):
    """The lines of a block, comments left out."""
    return sorted(
        line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


JOBS = block(LINES, "jobs")
REPORT = block(JOBS, "nightly-report")


class CiWorkflowTests(unittest.TestCase):
    def test_no_run_on_a_push(self):
        self.assertEqual(
            sorted(keys(block(LINES, "on"))), ["pull_request", "schedule", "workflow_dispatch"]
        )
        self.assertEqual(entries(block(block(LINES, "on"), "pull_request")), ["branches: [master]"])

    def test_the_workflow_itself_only_reads(self):
        self.assertEqual(entries(block(LINES, "permissions")), ["contents: read"])

    def test_only_the_nightly_report_may_write_and_only_issues(self):
        self.assertEqual(keys(JOBS), ["check", "harness", "nightly-report"])
        for job in ("check", "harness"):
            self.assertNotIn("permissions", keys(block(JOBS, job)))
        self.assertEqual(
            entries(block(REPORT, "permissions")),
            ["actions: read", "contents: read", "issues: write"],
        )
        writes = [line.strip() for line in LINES if re.search(r":\s*write\b", line)]
        self.assertEqual(writes, ["issues: write"])

    def test_the_nightly_report_runs_only_after_a_failed_scheduled_run(self):
        self.assertEqual(value(REPORT, "if"), "github.event_name == 'schedule' && failure()")
        self.assertEqual(value(REPORT, "needs"), "[check, harness]")

    def test_the_nightly_report_checks_out_nothing_and_reads_no_secret(self):
        text = "\n".join(REPORT)
        self.assertNotIn("uses:", text)
        self.assertNotIn("secrets.", "\n".join(LINES))
        self.assertEqual(value(block(REPORT, "env"), "GH_TOKEN"), "${{ github.token }}")
        # Workflow expressions reach the script through env only, never inside the script.
        script = REPORT[next(n for n, line in enumerate(REPORT) if line.strip() == "run: |") :]
        self.assertGreater(len(script), 10)
        self.assertNotIn("${{", "\n".join(script))

    def test_the_nightly_report_keeps_one_issue(self):
        text = "\n".join(REPORT)
        self.assertIn('--title "Nightly CI failed: $(date -u +%F)"', text)
        self.assertIn("--label bug --label P1", text)
        self.assertIn('startswith("Nightly CI failed:")', text)
        self.assertIn("gh issue comment", text)

    def test_the_harness_job_uses_the_hooks_runtime_check_paths(self):
        text = "\n".join(block(JOBS, "harness"))
        self.assertIn(f"grep -Eq '{hook_selection.SUITES['runtime-check'].pattern}'", text)
        for target in ("runtime-check", "deploy-check", "hook-cases"):
            self.assertIn(f"- run: make {target}\n        if: steps.harness.outputs.changed", text)

    def test_every_step_of_the_docker_job_waits_for_the_scope_decision(self):
        """A pull request that cannot affect the checks runs none of them (#369)."""
        steps = "\n".join(block(block(JOBS, "check"), "steps")).split("\n      - ")
        steps = [step for step in steps if step.strip() and not step.lstrip().startswith("#")]
        self.assertIn("actions/checkout@", steps[0])
        self.assertIn("id: scope", steps[1])
        self.assertIn('hook_selection.py ci-check >> "$GITHUB_OUTPUT"', steps[1])
        self.assertIn('"$GITHUB_EVENT_NAME" != pull_request', steps[1])
        self.assertEqual(len(steps), 9)
        for step in steps[2:]:
            self.assertIn("if: steps.scope.outputs.run == 'true'", step, step)


if __name__ == "__main__":
    unittest.main()
