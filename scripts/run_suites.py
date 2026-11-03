"""Run independent unittest scripts in parallel; print each suite's output when it ends.

Usage: run_suites.py [--jobs N] scripts/test_a.py scripts/test_b.py ...
Exit status is nonzero if any suite fails. Output stays grouped per suite, so a failure
reads like a serial run. Each suite already builds its own disposable repositories.
"""

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_suite(path):
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - fixed interpreter, caller-listed test scripts
        [sys.executable, "-B", path],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_PAGER": "cat"},
    )
    return path, result.returncode, result.stdout + result.stderr, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("suites", nargs="+")
    args = parser.parse_args()
    failed = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(run_suite, path) for path in args.suites]
        for future in as_completed(futures):
            path, code, output, seconds = future.result()
            print(f"=== {path}: {'ok' if code == 0 else 'FAILED'} ({seconds:.0f} s)", flush=True)
            if code:
                failed.append(path)
                print(output, flush=True)
    total = time.monotonic() - started
    if failed:
        print(f"{len(failed)} of {len(args.suites)} suites failed: {', '.join(failed)}")
        return 1
    print(f"all {len(args.suites)} suites passed in {total:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
