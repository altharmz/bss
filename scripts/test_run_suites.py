#!/usr/bin/env python3
"""scripts/run_suites.py: suites in parallel parts, one grouped result per suite."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run_suites import split

RUNNER = Path(__file__).resolve().parent / "run_suites.py"
SUITE = """import os, unittest
class A(unittest.TestCase):
    def test_one(self): open(os.environ["SEEN"], "a").write(f"one {os.getpid()}\\n")
    def test_two(self): open(os.environ["SEEN"], "a").write(f"two {os.getpid()}\\n")
    def test_three(self): {three}
if __name__ == "__main__":
    unittest.main()
"""


class RunSuitesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.seen = self.directory / "seen"

    def suite(self, name, three="pass"):
        path = self.directory / name
        path.write_text(SUITE.replace("{three}", three))
        return str(path)

    def run_suites(self, *args):
        return subprocess.run(  # noqa: S603 - fixed interpreter and repository script
            [sys.executable, "-B", str(RUNNER), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "SEEN": str(self.seen)},
        )

    def test_split_deals_every_test_once_into_nonempty_parts(self):
        tests = [f"A.test_{n}" for n in range(7)]
        for parts in (1, 2, 3, 7, 20):
            groups = split(tests, parts)
            self.assertEqual(sorted(sum(groups, [])), tests)
            self.assertEqual(len(groups), min(parts, 7))
            self.assertTrue(all(groups))

    def test_a_suite_runs_every_test_once_in_separate_parts(self):
        result = self.run_suites("--jobs", "3", self.suite("test_a.py"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all 1 suites passed", result.stdout)
        self.assertEqual(result.stdout.count("=== "), 1)
        seen = [line.split() for line in self.seen.read_text().splitlines()]
        self.assertEqual(sorted(name for name, _ in seen), ["one", "two"])
        self.assertEqual(len({pid for _, pid in seen}), 2)

    def test_one_part_runs_the_file_whole(self):
        result = self.run_suites("--parts", "1", self.suite("test_a.py"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len({line.split()[1] for line in self.seen.read_text().splitlines()}), 1)

    def test_a_failing_part_fails_its_suite_and_prints_only_that_output(self):
        good = self.suite("test_good.py")
        bad = self.suite("test_bad.py", three='self.fail("boom")')
        result = self.run_suites("--jobs", "3", good, bad)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"=== {good}: ok", result.stdout)
        self.assertIn(f"=== {bad}: FAILED", result.stdout)
        self.assertIn("AssertionError: boom", result.stdout)
        self.assertIn(f"1 of 2 suites failed: {bad}", result.stdout)

    def test_a_suite_that_cannot_be_listed_runs_whole_and_fails_itself(self):
        broken = self.directory / "test_broken.py"
        broken.write_text("import nonexistent_module_for_this_test\n")
        result = self.run_suites("--jobs", "2", str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ModuleNotFoundError", result.stdout)


if __name__ == "__main__":
    unittest.main()
