"""make_help.py: the grouped `make help` listing over the real and a temporary Makefile."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "make_help.py"
MAKEFILE = SCRIPTS.parent / "Makefile"
GROUPS = ("task", "stack", "test", "ui", "docs", "mcp", "loadtest", "telemetry", "ops")
OPS = {
    "deploy",
    "rollback",
    "verify",
    "deployed",
    "tunnel",
    "ssh",
    "remote",
    "deploy-web-install",
    "grafana-rules-check",
    "grafana-rules-apply",
}
TARGET_LINE = re.compile(r"^  (\S+)\s")


def run(makefile, group=""):
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository script
        [sys.executable, "-B", str(SCRIPT), str(makefile), group],
        capture_output=True,
        text=True,
        check=False,
    )


def targets(output):
    return [m.group(1) for m in map(TARGET_LINE.match, output.splitlines()) if m]


def documented_targets():
    """Independent scan: every `target: ... ## ` line, the way the old grep listed them."""
    names = []
    for line in MAKEFILE.read_text().splitlines():
        if "## " in line and ":" in line and not line.startswith(("\t", "#", " ")):
            name = line.split(":", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9_-]+", name):
                names.append(name)
    return names


class RealMakefileTests(unittest.TestCase):
    def test_every_group_renders(self):
        for group in ("", *GROUPS, "all"):
            with self.subTest(group=group):
                result = run(MAKEFILE, group)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(targets(result.stdout))

    def test_ops_group_is_the_human_only_targets(self):
        self.assertEqual(set(targets(run(MAKEFILE, "ops").stdout)), OPS)
        self.assertEqual(len(targets(run(MAKEFILE, "ops").stdout)), len(OPS))

    def test_default_output_hides_ops_and_shows_the_workflow(self):
        listed = set(targets(run(MAKEFILE).stdout))
        self.assertFalse(listed & OPS)
        self.assertFalse({t for t in listed if t.startswith(("loadtest", "telemetry-"))})
        self.assertIn("start-task", listed)
        self.assertIn("commit", listed)

    def test_default_output_ends_with_the_pointers(self):
        lines = run(MAKEFILE).stdout.splitlines()
        self.assertEqual(
            lines[-2:],
            [
                "More groups: make help GROUP=loadtest | telemetry | ops | all",
                "Human-only operations: make help-ops",
            ],
        )
        self.assertEqual(lines[-3], "")

    def test_all_lists_every_documented_target_once(self):
        listed = targets(run(MAKEFILE, "all").stdout)
        self.assertEqual(sorted(listed), sorted(documented_targets()))
        self.assertEqual(len(listed), len(set(listed)))

    def test_unknown_group_fails(self):
        result = run(MAKEFILE, "nope")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr.strip(),
            "make help: unknown GROUP 'nope'; groups: "
            "task stack test ui docs mcp loadtest telemetry ops all",
        )


class BadTagTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="bss-make-help-")
        self.addCleanup(directory.cleanup)
        self.makefile = Path(directory.name) / "Makefile"

    def check_rejected(self, line, target):
        self.makefile.write_text(f"good: ## [task] Fine\n\ttrue\n{line}\n\ttrue\n")
        result = run(self.makefile)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), f"make help: {target} has no valid group tag")

    def test_target_without_tag(self):
        self.check_rejected("untagged: ## Does a thing", "untagged")

    def test_target_with_unknown_tag(self):
        self.check_rejected("odd: ## [misc] Does a thing", "odd")

    def test_layout(self):
        self.makefile.write_text("a: ## [ops] First\nbb: dep ## [ops] Second\n")
        self.assertEqual(
            run(self.makefile, "ops").stdout,
            f"Human-only operations (deploy, VPS)\n  {'a':<26}First\n  {'bb':<26}Second\n",
        )


if __name__ == "__main__":
    unittest.main()
