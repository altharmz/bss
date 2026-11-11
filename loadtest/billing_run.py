"""Measure one daily billing run in the local load-test stack (#283, docs/loadtest.md).

`make loadtest-billing-run DATE=<run date> [CPUS=1]` runs this on the host
(stdlib only). The database must hold the dataset (`make loadtest-restore NAME=seed100k`)
and have no run for DATE yet; the run of the day before must exist, so the worker bills
DATE on its own.

1. resets pg_stat_statements;
2. recreates lt-worker with its clock pinned to DATE at BILLING_RUN_AT + 1 minute
   (WORKER_FAKE_NOW) and `cpus` = CPUS;
3. polls the run every POLL seconds (accounts done, status) and samples `docker stats`
   of lt-worker and lt-db;
4. when the run is no longer running, snapshots pg_stat_statements, stops lt-worker
   (its wake-up would go on with the queue and the prepaid sweep) and writes the report
   to loadtest/out/billing-run-<time>.json, with the digest of the run's results
   (loadtest/billing-digest.sql): two runs of one date on the same restored dataset must
   give the same digest.

Wall times are the host's: the worker's own timestamps come from the pinned clock.
"""

# A host tool: fixed docker commands, and SQL built only from a parsed date and the
# --tz / --run-at options of the operator who runs it.
# ruff: noqa: S603, S607, S608

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

LT = ["./scripts/task-compose", "--loadtest"]
POLL = 5.0


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        [
            *LT,
            "exec",
            "-T",
            "lt-db",
            "sh",
            "-c",
            'exec psql -X -q -At -F "\t" -v ON_ERROR_STOP=1'
            ' -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -',
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def container(service: str) -> str:
    return subprocess.run(
        [*LT, "ps", "-q", service], capture_output=True, text=True, check=True
    ).stdout.strip()


def cpu_sample(ids: dict[str, str]) -> dict[str, float]:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.ID}}\t{{.CPUPerc}}", *ids.values()],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    by_id = {
        line.split("\t")[0]: float(line.split("\t")[1].rstrip("%")) for line in out.splitlines()
    }
    return {name: by_id.get(cid[:12], 0.0) for name, cid in ids.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--cpus", default="1")
    parser.add_argument("--tz", default="Europe/Moscow")
    parser.add_argument("--run-at", default="00:30")
    args = parser.parse_args()

    if psql(f"SELECT 1 FROM billing_run WHERE run_date = '{args.date}'"):
        print(
            f"billing-run: a run of {args.date} exists; restore the dataset first", file=sys.stderr
        )
        return 2
    at = dtime.fromisoformat(args.run_at)
    fake = datetime.combine(args.date, dtime(at.hour, at.minute + 1), tzinfo=ZoneInfo(args.tz))
    members = int(
        psql(
            f"SELECT count(*) FROM billing_account WHERE opened_at < '{args.date}'::date::timestamp"
            f" AT TIME ZONE '{args.tz}'"
        )[0][0]
    )
    psql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements; SELECT pg_stat_statements_reset();")
    lscpu = subprocess.run(["lscpu", "-e"], capture_output=True, text=True).stdout
    env = {
        "LOADTEST_WORKER_FAKE_NOW": fake.isoformat(),
        "LOADTEST_WORKER_CPUS": args.cpus,
    }
    subprocess.run(
        [*LT, "up", "-d", "--no-deps", "--force-recreate", "lt-worker"],
        env={**os.environ, **env},
        check=True,
    )
    started = time.monotonic()
    ids = {"lt-worker": container("lt-worker"), "lt-db": container("lt-db")}
    samples: list[dict] = []
    status = "none"
    first_done: tuple[float, int] | None = None
    while True:
        rows = psql(
            "SELECT r.status, (SELECT count(*) FROM billing_run_account a WHERE a.run_id = r.id)"
            f" FROM billing_run r WHERE r.run_date = '{args.date}'"
        )
        elapsed = time.monotonic() - started
        if rows:
            status, done = rows[0][0], int(rows[0][1])
            if first_done is None and done > 0:
                first_done = (elapsed, done)
            if status != "running":
                break
            cpu = cpu_sample(ids)
            samples.append({"t": round(elapsed, 1), "done": done, **cpu})
            print(
                f"{elapsed:8.0f}s {done:7d} accounts  worker {cpu['lt-worker']:6.1f}%"
                f"  db {cpu['lt-db']:6.1f}%",
                flush=True,
            )
        time.sleep(POLL)
    duration = time.monotonic() - started
    stats = psql(
        "SELECT sum(calls) FROM pg_stat_statements;"
        "SELECT calls, round(total_exec_time::numeric, 1), round(mean_exec_time::numeric, 3), rows,"
        " left(regexp_replace(query, '\\s+', ' ', 'g'), 200)"
        " FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
    )
    subprocess.run([*LT, "stop", "lt-worker"], check=True)
    outcomes = dict(
        psql(
            "SELECT a.status, count(*) FROM billing_run_account a"
            " JOIN billing_run r ON r.id = a.run_id"
            f" WHERE r.run_date = '{args.date}' GROUP BY a.status"
        )
    )
    digest_sql = Path("loadtest/billing-digest.sql").read_text().replace(":'day'", f"'{args.date}'")
    digest = {name: {"count": int(n), "md5": md5} for name, n, md5 in psql(digest_sql)}
    billed = sum(int(n) for n in outcomes.values())
    steady = None
    if first_done is not None and samples and samples[-1]["done"] > first_done[1]:
        last = samples[-1]
        steady = (last["done"] - first_done[1]) / (last["t"] - first_done[0])
    busy = [s for s in samples if s["done"] > 0]
    report = {
        "run_date": str(args.date),
        "status": status,
        "cpus": args.cpus,
        "members": members,
        "outcomes": outcomes,
        "digest": digest,
        "wall_seconds": round(duration, 1),
        "accounts_per_second": round(billed / duration, 2),
        "steady_accounts_per_second": round(steady, 2) if steady else None,
        "first_outcome_after_seconds": round(first_done[0], 1) if first_done else None,
        "queries": int(stats[0][0]),
        "queries_per_account": round(int(stats[0][0]) / max(billed, 1), 1),
        "cpu_percent_mean": {
            name: round(sum(s[name] for s in busy) / max(len(busy), 1), 1) for name in ids
        },
        "top_statements": [
            {"calls": int(c), "total_ms": float(t), "mean_ms": float(m), "rows": int(r), "query": q}
            for c, t, m, r, q in stats[1:]
        ],
        "samples": samples,
        "lscpu": lscpu,
    }
    out = Path("loadtest/out") / f"billing-run-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    summary = {k: v for k, v in report.items() if k not in ("samples", "lscpu", "top_statements")}
    print(json.dumps(summary, indent=2))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
