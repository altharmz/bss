"""Local PostgreSQL telemetry. Never ingest command arguments, output or environment dumps."""

# SQL is built only from validated slugs, UUIDs, allowlisted names and integers.
# Docker is intentionally resolved from the local development toolchain's PATH.
# ruff: noqa: S607, S608

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPERATIONS = frozenset(
    {
        "up",
        "down",
        "start",
        "stop",
        "restart",
        "run",
        "exec",
        "build",
        "pull",
        "ps",
        "logs",
        "port",
        "rm",
        "config",
        "start-task",
        "format",
        "check",
        "review-task",
        "commit-attempt",
        "commit",
        "push-attempt",
        "push",
        "create-pr-attempt",
        "create-pr",
        "check-pr-attempt",
        "check-pr",
    }
)


def compose(*args):
    # No inherited Docker/Compose selectors, dotenv discovery or credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    return subprocess.run(  # noqa: S603 - fixed executable and argv, no shell
        [
            "docker",
            "--context",
            "default",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            "bss-telemetry",
            "-f",
            str(ROOT / "compose.telemetry.yaml"),
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def sql(statement):
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    result = subprocess.run(  # noqa: S603 - SQL on stdin, never shell interpolation
        [
            "docker",
            "--context",
            "default",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            "bss-telemetry",
            "-f",
            str(ROOT / "compose.telemetry.yaml"),
            "exec",
            "-T",
            "db",
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "telemetry",
            "-d",
            "telemetry",
        ],
        input=statement,
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("telemetry database unavailable or schema not initialized")


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def event_sql(
    task,
    operation,
    result,
    duration_ms,
    environ,
    slot=None,
    commit_hash=None,
    pr_number=None,
    pr_status=None,
    ci_state=None,
):
    match = re.fullmatch(r"issue-([1-9][0-9]*)-[a-z0-9-]+", task)
    if not match or len(task) > 80 or operation not in OPERATIONS:
        raise ValueError("invalid telemetry identity or operation")
    if type(result) is not int or (
        duration_ms is not None and (type(duration_ms) is not int or duration_ms < 0)
    ):
        raise ValueError("invalid telemetry result or duration")
    duration = "NULL" if duration_ms is None else str(duration_ms)
    if slot is not None and not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", slot):
        raise ValueError("invalid worker slot")
    metadata = "'{}'" if slot is None else literal('{"slot":"' + slot + '"}')
    if commit_hash is not None:
        if operation not in {"commit", "push"} or not re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", commit_hash
        ):
            raise ValueError("invalid commit identity")
        metadata = literal(json.dumps({"slot": slot, "commit_hash": commit_hash}))
    if any(value is not None for value in (pr_number, pr_status, ci_state)):
        if operation not in {"create-pr", "check-pr"}:
            raise ValueError("invalid PR metadata operation")
        if pr_number is not None and (type(pr_number) is not int or pr_number <= 0):
            raise ValueError("invalid PR number")
        if pr_status not in {
            None,
            "created",
            "existing",
            "missing",
            "ready",
            "failed",
            "pending",
            "blocked",
        }:
            raise ValueError("invalid PR status")
        if ci_state not in {None, "unknown", "passed", "failed", "pending"}:
            raise ValueError("invalid CI state")
        metadata = literal(
            json.dumps(
                {"slot": slot, "pr_number": pr_number, "pr_status": pr_status, "ci_state": ci_state}
            )
        )
    task_value = literal(task)
    statements = [
        "BEGIN;",
        f"INSERT INTO tasks(task_id, issue_number, branch) VALUES "
        f"({task_value}, {int(match[1])}, {task_value}) ON CONFLICT DO NOTHING;",
    ]
    session_value = "NULL"
    # Codex exposes a thread UUID. Other runtimes can explicitly provide a UUID;
    # never guess Claude session identity from a process id or scan conversation logs.
    external = environ.get("CODEX_THREAD_ID")
    explicit = environ.get("BSS_TELEMETRY_SESSION_ID")
    identity = external or explicit
    if identity:
        session = str(uuid.UUID(identity))
        provider = "codex" if external else "unknown"
        session_value = literal(f"{task}:{provider}:{session}")
        statements.append(
            "INSERT INTO agent_sessions(session_id, task_id, provider, external_session_id) "
            f"VALUES ({session_value}, {task_value}, {literal(provider)}, {literal(session)}) "
            "ON CONFLICT DO NOTHING;"
        )
    statements.extend(
        [
            "INSERT INTO workflow_events(task_id, session_id, event_type, result, "
            "duration_ms, metadata) "
            f"VALUES ({task_value}, {session_value}, {literal(operation)}, "
            f"{result}, {duration}, {metadata});",
            "COMMIT;",
        ]
    )
    return "\n".join(statements)


def record(
    task,
    operation,
    result,
    duration_ms,
    slot=None,
    commit_hash=None,
    pr_number=None,
    pr_status=None,
    ci_state=None,
):
    """Fail open, with a visible but content-free warning. Never print DB stderr."""
    try:
        sql(
            event_sql(
                task,
                operation,
                result,
                duration_ms,
                os.environ,
                slot,
                commit_hash,
                pr_number,
                pr_status,
                ci_state,
            )
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(
            "telemetry: event not recorded; run make telemetry-up to check storage", file=sys.stderr
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "migrate", "health"])
    args = parser.parse_args()
    try:
        if args.action in {"up", "down"}:
            result = compose(*(["up", "-d", "--wait"] if args.action == "up" else ["down"]))
            if result.returncode:
                raise RuntimeError("telemetry service operation failed")
        if args.action in {"up", "migrate"}:
            for migration in sorted((ROOT / "scripts/telemetry").glob("*.sql")):
                sql(migration.read_text())
        if args.action == "health":
            sql("SELECT 1 / count(*) FROM schema_versions WHERE version = 1;")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print(
            "telemetry: management failed; check local Docker and telemetry schema", file=sys.stderr
        )
        return 1
    print(f"telemetry: {args.action} succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
