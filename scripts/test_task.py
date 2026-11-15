"""scripts/task.py: the lifecycle target table, variable validation and dispatch."""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import task

SCRIPTS = Path(__file__).resolve().parent
MAKEFILE = SCRIPTS.parent / "Makefile"
RULE = re.compile(r"^([A-Za-z0-9_.-]+(?: [A-Za-z0-9_.-]+)*):(?![=:])\s*(.*)$")
PLAIN = "@python3 -B scripts/task.py $@"
FINISH = '@python3 -B "$(HARNESS_DIR)scripts/task.py" $@'
# Outer make invocations (make runtime-check ...) must not leak into the inner one.
MAKE_CONTROLS = {"MAKEFLAGS", "MAKELEVEL", "MFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS"}


def recipes(text):
    """{target: [recipe lines]} for the explicit rules of a Makefile."""
    found, current = {}, []
    for line in text.splitlines():
        if line.startswith("\t"):
            for name in current:
                found[name].append(line[1:])
            continue
        match = RULE.match(line)
        if not match or match.group(2).startswith(("export ", "unexport ")):
            current = []
            continue
        current = match.group(1).split()
        for name in current:
            found.setdefault(name, [])
    return found


class TableTests(unittest.TestCase):
    def test_every_table_target_runs_through_task_py_and_nothing_else_does(self):
        rules = recipes(MAKEFILE.read_text())
        for target in task.TARGETS:
            with self.subTest(target=target):
                expected = FINISH if target == "finish-task" else PLAIN
                self.assertEqual(rules.get(target), [expected])
        for target, lines in rules.items():
            if any("scripts/task.py" in line for line in lines):
                self.assertIn(target, task.TARGETS)

    def test_every_module_exists_with_a_main(self):
        for module, _arguments, _allowed in task.TARGETS.values():
            self.assertRegex((SCRIPTS / f"{module}.py").read_text(), r"\ndef main\(")


class DispatchTests(unittest.TestCase):
    def run_task(self, target, make_vars=None, **environ):
        module = MagicMock()
        module.main.return_value = 7
        values = {"BSS_MAKE_VARS": make_vars} if make_vars is not None else {}
        with (
            patch.dict(os.environ, {**values, **environ}),
            patch.object(task.importlib, "import_module", return_value=module) as imported,
            patch.object(sys, "argv", ["task.py"]),
            patch.object(sys, "stderr", io.StringIO()) as stderr,
        ):
            if make_vars is None:
                os.environ.pop("BSS_MAKE_VARS", None)
            os.environ.setdefault("JSON", "")
            result = task.main([target])
            argv = list(sys.argv)
            printed = stderr.getvalue()
        return result, imported, module, argv, printed

    def test_unknown_variable_fails_before_the_module_runs(self):
        result, imported, module, _argv, printed = self.run_task("commit", "MESAGE FILES")
        self.assertEqual(result, 1)
        imported.assert_not_called()
        module.main.assert_not_called()
        self.assertEqual(
            printed.strip(),
            "make commit: unknown variable MESAGE; allowed: "
            "DRY_RUN FILES FILES_FILE MESSAGE MESSAGE_FILE VERBOSE",
        )

    def test_several_unknown_variables_are_sorted_and_comma_separated(self):
        result, _imported, _module, _argv, printed = self.run_task("push", "ZED ALPHA")
        self.assertEqual(result, 1)
        self.assertIn("unknown variables ALPHA, ZED; allowed: VERBOSE", printed)

    def test_allowed_verbose_and_bss_variables_pass(self):
        result, imported, module, argv, _printed = self.run_task(
            "commit", "MESSAGE FILES VERBOSE BSS_TELEMETRY_AGENT"
        )
        self.assertEqual(result, 7)
        imported.assert_called_once_with("task_git")
        module.main.assert_called_once_with()
        self.assertEqual(argv, [str(SCRIPTS / "task_git.py"), "commit"])

    def test_environment_variables_are_not_checked(self):
        for make_vars in (None, ""):
            with self.subTest(make_vars=make_vars):
                result, _imported, module, _argv, _printed = self.run_task(
                    "push", make_vars, MESAGE="only in the environment"
                )
                self.assertEqual(result, 7)
                module.main.assert_called_once_with()

    def test_json_adds_the_flag_for_start_and_new_priority_task(self):
        for target, base in (("start-task", []), ("new-priority-task", ["--new-priority-task"])):
            with self.subTest(target=target):
                *_rest, argv, _printed = self.run_task(target, "JSON", JSON="1")
                self.assertEqual(argv[1:], [*base, "--json"])
                *_rest, argv, _printed = self.run_task(target, "", JSON="0")
                self.assertEqual(argv[1:], base)

    def test_unknown_target_returns_one(self):
        result, imported, *_rest = self.run_task("deploy")
        self.assertEqual(result, 1)
        imported.assert_not_called()


class MakeTests(unittest.TestCase):
    """The real Makefile exports the command-line names and finds the script next to it."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base)
        self.repo = self.base / "repo with space"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(MAKEFILE, self.repo / "Makefile")
        (self.repo / "scripts/task.py").write_text(
            "import os, sys\n"
            "print(os.environ.get('BSS_MAKE_VARS', ''))\n"
            "print(os.path.abspath(__file__))\n"
            "print(sys.argv[1])\n"
        )
        self.env = {k: v for k, v in os.environ.items() if k not in MAKE_CONTROLS}

    def make(self, *args, cwd):
        result = subprocess.run(  # noqa: S603
            [shutil.which("make"), "--no-print-directory", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_commit_exports_exactly_the_command_line_names(self):
        names, script, target = self.make("commit", "MESSAGE=x", "FILES=y", cwd=self.repo)
        self.assertEqual(sorted(names.split()), ["FILES", "MESSAGE"])
        self.assertEqual(Path(script), self.repo / "scripts/task.py")
        self.assertEqual(target, "commit")

    def test_finish_task_finds_the_script_next_to_the_makefile(self):
        elsewhere = self.base / "elsewhere"
        (elsewhere / "scripts").mkdir(parents=True)
        (elsewhere / "scripts/task.py").write_text("raise SystemExit('wrong script')\n")
        names, script, target = self.make(
            "-f", str(self.repo / "Makefile"), "finish-task", "SLOT=worker1", cwd=elsewhere
        )
        self.assertEqual(names, "SLOT")
        self.assertEqual(Path(script), self.repo / "scripts/task.py")
        self.assertEqual(target, "finish-task")


if __name__ == "__main__":
    unittest.main()
