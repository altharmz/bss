"""make coverage's report (#480): the per-source totals, the combined percentage from raw
line counts, the frontend line rule that matches json-summary, exclusions and failures."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import coverage_report

BACKEND = {
    "files": {
        "app/billing/core.py": {
            "summary": {"covered_lines": 8, "num_statements": 10},
            "missing_lines": [7, 3],
            "excluded_lines": [12],
        },
        "bss_mcp/server.py": {
            "summary": {"covered_lines": 5, "num_statements": 5},
            "missing_lines": [],
            "excluded_lines": [],
        },
    }
}

# Two statements start on line 1 (one hit, one not): the line counts as covered, as in
# istanbul-lib-coverage. Line 2 is hit, lines 3 and 4 are not.
FRONTEND = {
    "/web/src/screens/Parties.tsx": {
        "statementMap": {
            "0": {"start": {"line": 1}},
            "1": {"start": {"line": 1}},
            "2": {"start": {"line": 2}},
            "3": {"start": {"line": 3}},
            "4": {"start": {"line": 4}},
        },
        "s": {"0": 0, "1": 2, "2": 1, "3": 0, "4": 0},
    }
}
SUMMARY = {"total": {"lines": {"covered": 2, "total": 4}}}


class CoverageReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        source = self.dir / "frontend/src/screens/Parties.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("a\nb\n/* v8 ignore next */\nc\n", encoding="utf-8")

    def write(self, name, content):
        path = self.dir / name
        path.write_text(json.dumps(content) if not isinstance(content, str) else content)
        return path

    def run_report(self, backend=BACKEND, frontend=FRONTEND, summary=SUMMARY, statuses=("0", "0")):
        args = [
            "--backend", str(self.write("backend.json", backend)),
            "--backend-status", str(self.write("pytest-status", statuses[0])),
            "--frontend", str(self.write("final.json", frontend)),
            "--frontend-summary", str(self.write("summary.json", summary)),
            "--frontend-status", str(self.write("vitest-status", statuses[1])),
            "--out", str(self.dir / "out"),
            "--root", str(self.dir),
            "--commit", "abc123",
        ]  # fmt: skip
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = coverage_report.main(args)
        report = json.loads((self.dir / "out/report.json").read_text())
        return code, report, (self.dir / "out/report.md").read_text()

    def test_combined_total_weights_raw_line_counts(self):
        code, report, md = self.run_report()
        self.assertEqual(code, 0)
        self.assertEqual(
            report["totals"]["backend"],
            {"covered": 13, "executable": 15, "missing": 2, "percent": "86.67%"},
        )
        self.assertEqual(
            report["totals"]["frontend"],
            {"covered": 2, "executable": 4, "missing": 2, "percent": "50.00%"},
        )
        # (13 + 2) / (15 + 4), not the mean of the two percentages
        self.assertEqual(report["totals"]["combined"]["percent"], "78.95%")
        self.assertIn("| combined | 15 | 19 | 4 | 78.95% |", md)

    def test_files_list_missing_lines_as_ranges_with_the_largest_gap_first(self):
        _, report, md = self.run_report()
        self.assertEqual(
            [f["path"] for f in report["files"]][:2],
            ["app/billing/core.py", "frontend/src/screens/Parties.tsx"],
        )
        self.assertEqual(report["files"][1]["missing_lines"], [3, 4])
        self.assertIn("| `frontend/src/screens/Parties.tsx` | 2 | 3-4 |", md)
        self.assertNotIn("bss_mcp/server.py` |", md)

    def test_exclusions_are_listed_from_both_sources(self):
        _, _, md = self.run_report()
        self.assertIn("- `app/billing/core.py`: 12", md)
        self.assertIn("- `frontend/src/screens/Parties.tsx`: 3", md)

    def test_a_failed_suite_fails_the_target_after_the_report(self):
        code, report, md = self.run_report(statuses=("1", "0"))
        self.assertEqual(code, 1)
        self.assertEqual(report["statuses"], {"backend": 1, "frontend": 0})
        self.assertIn("backend suite exited with 1", md)
        self.assertIn("78.95%", md)  # the numbers are still there

    def test_a_frontend_count_that_disagrees_with_json_summary_is_a_problem(self):
        code, report, _ = self.run_report(summary={"total": {"lines": {"covered": 3, "total": 4}}})
        self.assertEqual(code, 1)
        self.assertIn("differ from json-summary", report["problems"][0])

    def test_a_missing_report_has_no_combined_total(self):
        args_backend = self.dir / "absent.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = coverage_report.main([
                "--backend", str(args_backend),
                "--frontend", str(self.write("final.json", FRONTEND)),
                "--out", str(self.dir / "out"), "--root", str(self.dir),
            ])  # fmt: skip
        report = json.loads((self.dir / "out/report.json").read_text())
        self.assertEqual(code, 1)
        self.assertNotIn("combined", report["totals"])
        self.assertIn("backend report missing", report["problems"][0])


if __name__ == "__main__":
    unittest.main()
