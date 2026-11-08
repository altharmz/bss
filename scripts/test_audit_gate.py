"""scripts/audit_gate.py: which pip-audit and npm audit findings block the security workflow (#241).

Each case pipes an inline sample of the tool's JSON into the gate with a fixed --today and
a test-owned ignore file and lockfile. Stdlib only.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts/audit_gate.py"
TODAY = "2026-11-08"

PIP_CLEAN = {"dependencies": [{"name": "fastapi", "version": "0.120.0", "vulns": []}], "fixes": []}
PIP_FINDING = {
    "dependencies": [
        {
            "name": "certifi",
            "version": "2026.7.22",
            "vulns": [
                {
                    "id": "PYSEC-2026-4320",
                    "fix_versions": ["2026.10.18"],
                    "aliases": ["GHSA-6q48-6c7w-9gw2"],
                    "description": "x",
                }
            ],
        }
    ],
    "fixes": [],
}

LOCK = {
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "web"},
        "node_modules/react": {"version": "19.0.0"},
        "node_modules/vite": {"version": "6.0.0", "dev": True},
    },
}


def npm_report(package, severity):
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            package: {
                "name": package,
                "severity": severity,
                "isDirect": True,
                "via": [
                    {
                        "source": 1100001,
                        "name": package,
                        "dependency": package,
                        "title": "t",
                        "url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                        "severity": severity,
                        "range": "<9",
                    }
                ],
                "effects": [],
                "range": "<9",
                "nodes": [f"node_modules/{package}"],
                "fixAvailable": {"name": package, "version": "9.0.0", "isSemVerMajor": True},
            }
        },
        "metadata": {},
    }


class AuditGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bss-audit-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "lock.json").write_text(json.dumps(LOCK))
        self.ignore("")

    def ignore(self, text):
        (self.tmp / "ignore.txt").write_text("# format comment\n" + text)

    def gate(self, tool, data):
        stdin = data if isinstance(data, str) else json.dumps(data)
        return subprocess.run(  # noqa: S603 - the gate under test
            [
                sys.executable,
                "-B",
                str(GATE),
                "--tool",
                tool,
                "--today",
                TODAY,
                "--ignore",
                str(self.tmp / "ignore.txt"),
                "--lockfile",
                str(self.tmp / "lock.json"),
            ],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def assertExit(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def test_clean_input_passes(self):
        self.assertExit(self.gate("pip", PIP_CLEAN), 0)
        self.assertExit(self.gate("npm", {"auditReportVersion": 2, "vulnerabilities": {}}), 0)

    def test_high_prod_npm_finding_blocks(self):
        result = self.gate("npm", npm_report("react", "high"))
        self.assertExit(result, 1)
        self.assertIn("react 19.0.0 GHSA-aaaa-bbbb-cccc high react@9.0.0", result.stdout)

    def test_high_dev_only_npm_finding_passes(self):
        self.assertExit(self.gate("npm", npm_report("vite", "high")), 0)

    def test_critical_dev_only_npm_finding_blocks(self):
        self.assertExit(self.gate("npm", npm_report("vite", "critical")), 1)

    def test_package_missing_from_lockfile_counts_as_prod(self):
        self.assertExit(self.gate("npm", npm_report("ghost", "high")), 1)

    def test_pip_finding_blocks(self):
        result = self.gate("pip", PIP_FINDING)
        self.assertExit(result, 1)
        self.assertIn("certifi 2026.7.22 PYSEC-2026-4320 unknown 2026.10.18", result.stdout)

    def test_listed_finding_passes(self):
        self.ignore(
            "PYSEC-2026-4320 2026-12-08 baseline, see #1\nGHSA-aaaa-bbbb-cccc 2026-12-08 baseline\n"
        )
        self.assertExit(self.gate("pip", PIP_FINDING), 0)
        self.assertExit(self.gate("npm", npm_report("react", "high")), 0)

    def test_listed_alias_passes(self):
        self.ignore("GHSA-6q48-6c7w-9gw2 2026-12-08 baseline\n")
        self.assertExit(self.gate("pip", PIP_FINDING), 0)

    def test_entry_past_review_by_blocks(self):
        self.ignore("PYSEC-2026-4320 2026-11-07 baseline\n")
        result = self.gate("pip", PIP_FINDING)
        self.assertExit(result, 1)
        self.assertIn("has passed", result.stdout)

    def test_expired_entry_blocks_even_with_clean_input(self):
        self.ignore("PYSEC-2026-4320 2026-11-07 baseline\n")
        self.assertExit(self.gate("pip", PIP_CLEAN), 1)

    def test_review_by_on_the_day_and_at_60_days_passes(self):
        self.ignore("PYSEC-2026-4320 2026-11-08 a\nGHSA-6q48-6c7w-9gw2 2027-01-07 b\n")
        self.assertExit(self.gate("pip", PIP_FINDING), 0)

    def test_review_by_more_than_60_days_ahead_blocks(self):
        self.ignore("PYSEC-2026-4320 2027-01-08 baseline\n")
        result = self.gate("pip", PIP_FINDING)
        self.assertExit(result, 1)
        self.assertIn("more than 60 days", result.stdout)

    def test_malformed_ignore_line_blocks(self):
        for line in ("PYSEC-2026-4320 2026-12-08\n", "PYSEC-2026-4320 08.12.2026 reason\n"):
            with self.subTest(line=line):
                self.ignore(line)
                result = self.gate("pip", PIP_CLEAN)
                self.assertExit(result, 1)
                self.assertIn("malformed", result.stdout)

    def test_unknown_json_shape_exits_2(self):
        cases = [
            ("pip", {"something": []}),
            ("pip", "not json"),
            ("pip", ""),
            ("npm", {"auditReportVersion": 1, "advisories": {}}),
            ("npm", {"error": {"code": "ENOLOCK", "summary": "no lockfile"}}),
            (
                "npm",
                {
                    "auditReportVersion": 2,
                    "vulnerabilities": {"x": {"via": [{"name": "x", "severity": "?"}]}},
                },
            ),
        ]
        for tool, data in cases:
            with self.subTest(tool=tool, data=data):
                result = self.gate(tool, data)
                self.assertExit(result, 2)
                self.assertRegex(result.stderr, r"^audit_gate: ")

    def test_unreadable_lockfile_exits_2(self):
        (self.tmp / "lock.json").unlink()
        result = self.gate("npm", npm_report("react", "high"))
        self.assertExit(result, 2)
        self.assertRegex(result.stderr, r"^audit_gate: .*lockfile")


if __name__ == "__main__":
    unittest.main()
