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

    def generate(self, *args):
        directory = self.root / ".progress"
        directory.mkdir(exist_ok=True)
        for name, data in (
            ("backend.json", self.backend),
            ("ui.json", []),
            ("ui-tested.json", []),
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


if __name__ == "__main__":
    unittest.main()
