"""Run independent unittest scripts in parallel; print each suite's output when it ends.

Usage: run_suites.py [--jobs N] [--parts N] scripts/test_a.py scripts/test_b.py ...
Exit status is nonzero if any suite fails. Output stays grouped per suite, so a failure
reads like a serial run. Each suite already builds its own disposable repositories.

A suite runs in up to --parts parts (default: the number of jobs), its tests dealt out
in turn and each part started as `python test_a.py Class.test ...`, so the longest suite
does not set the wall time: the tests of a suite must not depend on each other.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

# Harness tests must never write to the shared telemetry database.
ENV = {"GIT_PAGER": "cat", "BSS_TELEMETRY_DISABLED": "1"}


def names(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from names(item)
        else:
            yield item.id().split(".", 1)[1]


def list_tests(path):
    """Print the suite's tests as `Class.method` names (JSON); run in its own process."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec = importlib.util.spec_from_file_location("suite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(json.dumps(sorted(names(unittest.defaultTestLoader.loadTestsFromModule(module)))))
    return 0


def execute(*args):
    return subprocess.run(  # noqa: S603 - fixed interpreter, caller-listed test scripts
        [sys.executable, "-B", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **ENV},
    )


def split(tests, parts):
    """Deal the tests into at most `parts` nonempty groups, in turn."""
    count = max(1, min(parts, len(tests)))
    return [tests[index::count] for index in range(count)]


def plan(path, parts):
    """The parts of one suite: lists of test names, or [[]] for the whole file at once."""
    if parts < 2:
        return [[]]
    listed = execute(__file__, "--list", path)
    try:
        tests = json.loads(listed.stdout) if listed.returncode == 0 else []
    except ValueError:
        tests = []
    # A suite that cannot be listed (or has no tests) runs whole and reports its own error.
    return split(tests, parts) if tests else [[]]


def run_part(path, tests):
    started = time.monotonic()
    result = execute(path, *tests)
    return path, result.returncode, result.stdout + result.stderr, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument("--parts", type=int, default=None)
    parser.add_argument("--list", metavar="SUITE", help=argparse.SUPPRESS)
    parser.add_argument("suites", nargs="*")
    args = parser.parse_args()
    if args.list:
        return list_tests(args.list)
    if not args.suites:
        parser.error("no suites given")
    jobs = max(1, args.jobs)
    parts = jobs if args.parts is None else args.parts
    started = time.monotonic()
    failed = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        plans = list(pool.map(lambda path: plan(path, parts), args.suites))
        # Per suite: parts still running, the worst exit status, failed output, summed time.
        state = {
            path: [len(parts_), 0, "", 0.0] for path, parts_ in zip(args.suites, plans, strict=True)
        }
        futures = [
            pool.submit(run_part, path, tests)
            for path, parts_ in zip(args.suites, plans, strict=True)
            for tests in parts_
        ]
        for future in as_completed(futures):
            path, code, output, seconds = future.result()
            entry = state[path]
            entry[0] -= 1
            entry[1] = entry[1] or code
            entry[2] += output if code else ""
            entry[3] += seconds
            if entry[0]:
                continue
            print(f"=== {path}: {'FAILED' if entry[1] else 'ok'} ({entry[3]:.0f} s)", flush=True)
            if entry[1]:
                failed.append(path)
                print(entry[2], flush=True)
    total = time.monotonic() - started
    if failed:
        print(f"{len(failed)} of {len(args.suites)} suites failed: {', '.join(failed)}")
        return 1
    print(f"all {len(args.suites)} suites passed in {total:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
