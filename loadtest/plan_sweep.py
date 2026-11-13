"""Host orchestrator for #327; all database/Python dependency work runs in containers."""

# Fixed local task commands, no shell and no credential output.
# ruff: noqa: S603, S607
import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

COMPOSE = ["./scripts/task-compose"]


def command(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("all", "prepare", "collect", "export", "measure"), default="all"
    )
    parser.add_argument("--run", default="")
    args = parser.parse_args()
    name = args.run or f"plan-sweep-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    if not name.startswith("plan-sweep-") or Path(name).name != name:
        parser.error("--run must be a plan-sweep-* directory name, not a path")
    out = Path("loadtest/out") / name
    out.mkdir(parents=True, exist_ok=True)
    inside = f"/app/{out}"
    if args.phase in ("all", "prepare", "collect"):
        if (out / "shapes.json").exists():
            parser.error("collection already exists; use a fresh run or --phase measure")
        if any((out / "capture").glob("capture-*.json")):
            parser.error("capture files already exist; use a fresh run to avoid mixing suites")
        # Cloned databases have separate statistics entries, even for identical SQL.
        # Reserve enough slots for the full run; ordinary local tests keep 10k slots.
        os.environ["BSS_TEST_STAT_MAX"] = "1000000"
        command([*COMPOSE, "up", "-d", "--wait", "db"])
        command(
            [
                *COMPOSE,
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
                " -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
                " SELECT pg_stat_statements_reset();'",
            ]
        )
        print(f"collecting the full suite into {out}", flush=True)
    if args.phase in ("all", "collect"):
        command(["make", "test", f"PLAN_CAPTURE_DIR={inside}/capture"])
    elif args.phase == "prepare":
        print(
            f"next: make test PLAN_CAPTURE_DIR={inside}/capture BSS_TEST_STAT_MAX=1000000",
            flush=True,
        )
    if args.phase in ("all", "collect", "export"):
        command(
            [
                *COMPOSE,
                "run",
                "--rm",
                "--no-deps",
                "oneoff",
                "python",
                "-m",
                "loadtest.plan_capture",
                inside,
            ]
        )
    if args.phase in ("all", "measure"):
        context = {
            "lscpu": subprocess.run(
                ["lscpu", "-e"], capture_output=True, text=True, check=True
            ).stdout,
            "sut_cpuset": os.environ.get("LOADTEST_SUT_CPUS", "4-7"),
        }
        (out / "context.json").write_text(json.dumps(context, indent=1))
        command(
            [*COMPOSE, "--loadtest", "rm", "--stop", "--force", "lt-app", "lt-worker", "lt-mcp"]
        )
        command([*COMPOSE, "--loadtest", "up", "-d", "--wait", "lt-db"])
        command(
            [
                *COMPOSE,
                "--loadtest",
                "run",
                "--rm",
                "--no-deps",
                "lt-tools",
                "python",
                "-m",
                "loadtest.plan_measure",
                inside,
            ]
        )
    print(f"plan sweep: {out}", flush=True)


if __name__ == "__main__":
    main()
