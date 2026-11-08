"""Local progress reports stay outside committed docs and preserve validation failures."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR = Path(__file__).resolve().parent / "progress/generate.py"


class ProgressGenerateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bss-progress-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = {
            "items": [
                {
                    "id": "db.example",
                    "area": "db",
                    "stage": 1,
                    "title": "Example",
                    "table": "example",
                }
            ],
            "excluded": [],
        }
        self.backend = {
            "scope": self.scope,
            "tables": ["example"],
            "openapi": [],
            "capabilities": {"passed": [], "failed": [], "seen": []},
            "tested": {"tables": ["example"], "api": []},
        }
        (self.root / "scope.yaml").write_text(json.dumps(self.scope))
        self.ui_markers = []
        self.ui_tested = []

    def generate(self, *args):
        directory = self.root / ".progress"
        directory.mkdir(exist_ok=True)
        for name, data in (
            ("backend.json", self.backend),
            ("ui.json", self.ui_markers),
            ("ui-tested.json", self.ui_tested),
        ):
            (directory / name).write_text(json.dumps(data))
        return subprocess.run(  # noqa: S603 - fixed local script, disposable working directory
            [sys.executable, "-B", str(GENERATOR), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_defaults_write_local_reports_without_creating_docs(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".progress/PROGRESS.md").is_file())
        report = json.loads((self.root / ".progress/status.json").read_text())
        self.assertEqual(report["areas"]["db"], {"items": 1, "implemented": 1, "tested": 1})
        self.assertFalse((self.root / "docs").exists())

    def test_custom_output_parents_are_created(self):
        result = self.generate(
            "--md", "reports/md/progress.md", "--json", "reports/json/status.json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "reports/md/progress.md").is_file())
        self.assertTrue((self.root / "reports/json/status.json").is_file())

    def test_unscoped_items_still_write_reports_and_exit_one(self):
        self.backend["tables"].append("unscoped")
        result = self.generate()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads((self.root / ".progress/status.json").read_text())
        self.assertEqual(report["unscoped"], [{"area": "db", "what": "table unscoped"}])
        self.assertIn("table unscoped", (self.root / ".progress/PROGRESS.md").read_text())

    def ui_item(self, ident):
        self.scope["items"].append({"id": ident, "area": "ui", "stage": 1, "title": ident})

    def ui_status(self, ident):
        report = json.loads((self.root / ".progress/status.json").read_text())
        return next(i for i in report["items"] if i["id"] == ident)

    def test_ui_marker_seen_on_screen_by_a_passing_test_counts_as_implemented(self):
        # Markers built at run time (template literals, props) are invisible to the static
        # collector, but expectUi proves the element rendered.
        self.ui_item("ui.dynamic")
        self.ui_tested.append("ui.dynamic")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ui_status("ui.dynamic")["implemented"], True)
        self.assertEqual(self.ui_status("ui.dynamic")["tested"], True)

    def test_ui_marker_neither_in_source_nor_seen_stays_missing(self):
        self.ui_item("ui.absent")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ui_status("ui.absent")["implemented"], False)

    def test_ui_marker_in_source_only_is_implemented_but_untested(self):
        self.ui_item("ui.static")
        self.ui_markers.append({"marker": "ui.static", "file": "a.tsx", "line": 1})
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ui_status("ui.static")["implemented"], True)
        self.assertEqual(self.ui_status("ui.static")["tested"], False)


if __name__ == "__main__":
    unittest.main()
