"""Run one k6 load-test scenario against the local load-test stack (#281, docs/loadtest.md).

`make loadtest SCENARIO=<name> [MULT=1] [DURATION=30m]` runs this on the host (stdlib
only). Scenarios: smoke, steady, stress, soak, billing-night, prod-shaped. It

1. (re)starts the system under test with the scenario's settings (the usage feed rate, a
   fresh /metrics token; prod-shaped: today's production limits) and waits for the
   worker's first wake-up to end, so the measurement does not start inside it;
2. records the context (`lscpu -e`, the cpusets and limits of the lt-* containers, the
   host's other running containers with their CPU %) and samples k6's ids from the
   dataset (loadtest/k6-data.sql);
3. resets pg_stat_statements, starts the samplers (`docker stats` every 10 s, /metrics
   every 15 s in lt-scrape) and runs k6 (lt-k6, its own cpuset) with the operators'
   password and a throwaway read-only MCP token in its environment only;
4. collects the k6 summary, the top statements, the feed lag (the worker's
   `stub usage load:` lines) and the pool timeouts, and writes report.json and report.md
   with the verdict. The MCP token is revoked at the end.

Everything goes to loadtest/out/<scenario>-<time>/ (ignored by git); no secret is written
there, which the runner checks before it ends.
"""

# A host tool: fixed docker commands; SQL built only from the database's own values.
# ruff: noqa: S603, S607, S608

import argparse
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LT = ["./scripts/task-compose", "--loadtest"]
SUT = ["lt-db", "lt-app", "lt-worker", "lt-mcp"]
OUT = Path("loadtest/out")
SECRET = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / (
    "bss/loadtest/operator-password"
)
GROUPS = ["read", "list", "write", "auth", "ui", "mcp", "usage-post"]
SCENARIOS = ("smoke", "steady", "stress", "soak", "billing-night", "prod-shaped")
P95, P99, ERRORS = 1000.0, 1500.0, 0.001
LAG_LIMIT = 300.0  # the feed lag target, seconds
RUN_LIMIT = 90 * 60.0  # the 100k daily run, seconds
DUMP_AFTER = 105 * 60.0  # the 02:15 backup's slot after the 00:30 run start
RSS_GROWTH = 0.20
# today's production limits (compose.prod.yaml), for the prod-shaped scenario
PROD_SHAPED = {
    "LOADTEST_APP_CPUS": "0.5",
    "LOADTEST_APP_MEM": "384m",
    "LOADTEST_WEB_CONCURRENCY": "1",
    "LOADTEST_DB_POOL_SIZE": "10",
    "LOADTEST_WORKER_CPUS": "0.25",
    "LOADTEST_DB_MEM": "512m",
    "LOADTEST_DB_SHARED_BUFFERS": "128MB",
    "LOADTEST_DB_CACHE": "384MB",
}
FEED_RATE = 23.0  # records/s, daily average (2M a day)
PEAK_HOUR, NIGHT_HOUR = 20, 4  # the shape's hours: 70 records/s and 8 records/s at 23/s
# bss-mcp allows 60 calls a minute per principal (MCP_RATE_PER_MIN, as in production): the
# mcp group's calls are spread over enough service tokens to keep each one under this
MCP_RATE, MCP_PER_TOKEN = 2.0, 40.0  # calls/s at 1x; calls a minute one token carries


def run(cmd: list[str], env: dict[str, str] | None = None, **kw) -> str:
    return subprocess.run(
        cmd, env=env or os.environ.copy(), capture_output=True, text=True, check=True, **kw
    ).stdout


def psql(sql: str) -> str:
    return run(
        [
            *LT,
            "exec",
            "-T",
            "lt-db",
            "sh",
            "-c",
            'exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -',
        ],
        input=sql,
    )


def container(service: str) -> str:
    return run([*LT, "ps", "-q", service]).strip()


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


# ---------- the context ----------


def context(env: dict[str, str]) -> dict:
    ids = {s: container(s) for s in SUT}
    limits = {}
    for service, cid in ids.items():
        info = json.loads(run(["docker", "inspect", cid]))[0]["HostConfig"]
        limits[service] = {
            "cpuset": info["CpusetCpus"],
            "cpus": info["NanoCpus"] / 1e9 or None,
            "memory_mb": info["Memory"] // 2**20 or None,
        }
    mine = set(ids.values())
    others = []
    for line in run(
        ["docker", "stats", "--no-stream", "--format", "{{.ID}}\t{{.Name}}\t{{.CPUPerc}}"]
    ).splitlines():
        cid, name, cpu = line.split("\t")
        if not any(c.startswith(cid) for c in mine):
            others.append({"name": name, "cpu": cpu})
    return {
        "lscpu": run(["lscpu", "-e"]),
        "sut": limits,
        "k6_cpuset": env.get("LOADTEST_K6_CPUS", "8-9"),
        "sut_cpuset": env.get("LOADTEST_SUT_CPUS", "4-7"),
        "other_containers": others,
        "host": "laptop CPU (Ryzen 5 PRO 4650U) in WSL2, the SUT on a 4-CPU cpuset; not a server",
    }


# ---------- samplers ----------


def parse_mem(text: str) -> float:
    value = text.split("/")[0].strip()
    number = float(re.match(r"[\d.]+", value).group())
    unit = value[len(re.match(r"[\d.]+", value).group()) :].strip()
    return number * {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(unit, 1)


class Stats(threading.Thread):
    """`docker stats` of the SUT every 10 s, to stats.jsonl."""

    def __init__(self, path: Path, ids: dict[str, str]) -> None:
        super().__init__(daemon=True)
        self.path, self.ids, self.stop = path, ids, threading.Event()

    def run(self) -> None:
        names = {cid[:12]: s for s, cid in self.ids.items()}
        with self.path.open("a") as f:
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    out = run(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{.ID}}\t{{.CPUPerc}}\t{{.MemUsage}}",
                            *self.ids.values(),
                        ]
                    )
                    row = {"t": datetime.now(UTC).isoformat(timespec="seconds")}
                    for line in out.splitlines():
                        cid, cpu, mem = line.split("\t")
                        row[names.get(cid, cid)] = {
                            "cpu": float(cpu.rstrip("%")),
                            "mem_mb": round(parse_mem(mem), 1),
                        }
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                except (subprocess.CalledProcessError, ValueError, AttributeError) as error:
                    f.write(json.dumps({"error": str(error)}) + "\n")
                self.stop.wait(max(0.0, 10 - (time.monotonic() - started)))


# ---------- analysis ----------


def metric(summary: dict, name: str) -> dict | None:
    m = summary["metrics"].get(name)
    return m["values"] if m else None


def groups_of(summary: dict, seconds: float, step: str | None = None) -> dict:
    out = {}
    for g in GROUPS:
        suffix = f"grp:{g}" + (f",step:{step}" if step else "")
        dur = metric(summary, f"http_req_duration{{{suffix}}}")
        failed = metric(summary, f"http_req_failed{{{suffix}}}")
        if not dur or not dur.get("count"):
            continue
        row = {
            "count": int(dur["count"]),
            "rps": round(dur["count"] / seconds, 2) if seconds else None,
            "p50": round(dur["med"], 1),
            "p95": round(dur["p(95)"], 1),
            "p99": round(dur["p(99)"], 1),
            "max": round(dur["max"], 1),
            "error_rate": round(failed["rate"], 5) if failed else None,
        }
        row["pass"] = row["p95"] < P95 and row["p99"] < P99 and (row["error_rate"] or 0) < ERRORS
        out[g] = row
    return out


def container_stats(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if "t" in r]
    out = {}
    for service in SUT:
        samples = [r[service] for r in rows if service in r]
        if not samples:
            continue
        mem = [s["mem_mb"] for s in samples]
        cpu = [s["cpu"] for s in samples]
        n = max(1, len(mem) // 10)  # the first and the last tenth of the run
        first, last = sum(mem[:n]) / n, sum(mem[-n:]) / n
        out[service] = {
            "cpu_avg": round(sum(cpu) / len(cpu), 1),
            "cpu_max": round(max(cpu), 1),
            "mem_avg_mb": round(sum(mem) / len(mem), 1),
            "mem_max_mb": round(max(mem), 1),
            "mem_growth": round((last - first) / first, 3) if first else None,
        }
    return out


FEED_LINE = re.compile(
    r'"ts": "([^"]+)".*stub usage load: queued=(\d+) made=(\d+) dropped=\d+ lag_seconds=([\d.]+)'
)


def feed_lag(worker_log: str, since: datetime | None = None) -> dict:
    """The feed's lag from the worker's `stub usage load:` lines; with `since`, only the
    lines logged from then on (the measured window, not the worker's start-up)."""
    lines = [
        (int(queued), int(made), float(lag))
        for ts, queued, made, lag in FEED_LINE.findall(worker_log)
        if since is None or datetime.fromisoformat(ts.replace("Z", "+00:00")) >= since
    ]
    queued, made, lags = ([line[i] for line in lines] for i in range(3))
    return {
        "samples": len(lags),
        "max_lag_seconds": max(lags) if lags else None,
        "last_lag_seconds": lags[-1] if lags else None,
        "max_queued": max(queued) if queued else None,
        "records_made": made[-1] if made else None,
    }


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        f"# {report['scenario']} ({report['started']})",
        "",
        f"Verdict: **{'PASS' if report['pass'] else 'FAIL'}**. {report['context']['host']}.",
        f"SUT cpuset {report['context']['sut_cpuset']},"
        f" k6 cpuset {report['context']['k6_cpuset']}.",
        "",
        "| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | Pass |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g, r in report["groups"].items():
        lines.append(
            f"| {g} | {r['count']} | {r['rps']} | {r['p50']} | {r['p95']} | {r['p99']} |"
            f" {r['error_rate']:.3%} | {'yes' if r['pass'] else 'no'} |"
        )
    lines += ["", "| Container | CPU avg % | CPU max % | RAM avg MB | RAM max MB | RAM growth |",
              "|---|---|---|---|---|---|"]  # fmt: skip
    for c, r in report["containers"].items():
        lines.append(
            f"| {c} | {r['cpu_avg']} | {r['cpu_max']} | {r['mem_avg_mb']} | {r['mem_max_mb']} |"
            f" {r['mem_growth']:.1%} |"
        )
    lines += ["", "```", json.dumps(report["checks"], indent=1), "```", ""]
    if report.get("steps"):
        lines += ["Steps:", "```", json.dumps(report["steps"], indent=1), "```", ""]
    if report.get("billing"):
        lines += ["Billing night:", "```", json.dumps(report["billing"], indent=1), "```", ""]
    lines += ["Top statements:", "```", report["pg_stats"], "```"]
    path.write_text("\n".join(lines) + "\n")


# ---------- billing night ----------


def billing_watch(run_date: date, result: dict, stop: threading.Event) -> None:
    """Wait for the catch-up run of run_date, time it, and start a pg_dump -Fc of the
    database DUMP_AFTER after the run's start (the 02:15 backup's slot)."""
    started = None
    while not stop.is_set():
        row = psql(
            "SELECT status, extract(epoch FROM started_at), extract(epoch FROM finished_at),"
            f" member_count, ok_count, failed_count FROM billing_run WHERE run_date = '{run_date}'"
            " AND kind = 'daily' ORDER BY attempt DESC LIMIT 1"
        ).strip()
        if row:
            status, s, f, members, ok, failed = (row.split("|") + [""] * 6)[:6]
            if started is None and s:
                started = time.time()
                result["run_started"] = datetime.fromtimestamp(float(s), UTC).isoformat()
                log(f"billing run of {run_date} started")
            result.update(status=status, members=members, ok=ok, failed=failed)
            if f and "run_seconds" not in result:
                result["run_seconds"] = round(float(f) - float(s), 1)
                result["run_finished"] = datetime.fromtimestamp(float(f), UTC).isoformat()
                log(f"billing run finished: {status} in {result['run_seconds']} s")
        due = started is not None and time.time() - started >= DUMP_AFTER
        if due and "dump_seconds" not in result:
            log("pg_dump -Fc started (the backup slot)")
            t0 = time.monotonic()
            subprocess.run(
                [*LT, "exec", "-T", "lt-db", "sh", "-c",
                 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" | wc -c'],
                capture_output=True, text=True, check=False,
            )  # fmt: skip
            result["dump_seconds"] = round(time.monotonic() - t0, 1)
            result["dump_started"] = datetime.now(UTC).isoformat(timespec="seconds")
            log(f"pg_dump done in {result['dump_seconds']} s")
            return
        stop.wait(10)


def window_latency(csv_gz: Path, start: str, end: str) -> dict:
    """p95/p99 per group of the requests inside [start, end) from k6's CSV output."""
    import csv
    import gzip

    lo = datetime.fromisoformat(start).timestamp()
    hi = datetime.fromisoformat(end).timestamp()
    values: dict[str, list[float]] = {}
    with gzip.open(csv_gz, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["metric_name"] != "http_req_duration":
                continue
            t = float(row["timestamp"])
            if not lo <= t < hi:
                continue
            grp = row.get("extra_tags") or ""
            match = re.search(r"grp=([a-z-]+)", grp)
            g = row.get("grp") or (match.group(1) if match else "")
            values.setdefault(g, []).append(float(row["metric_value"]))
    out = {}
    for g, v in values.items():
        v.sort()
        out[g] = {
            "count": len(v),
            "p95": round(v[int(0.95 * (len(v) - 1))], 1),
            "p99": round(v[int(0.99 * (len(v) - 1))], 1),
        }
    return out


# ---------- main ----------


SETTLED_LAG = 120.0  # seconds: the feed has caught up with what the start-up left waiting


def settled(worker_log: str, feed: bool) -> bool:
    """The worker finished a wake-up and, with the feed on, a later fetch found the feed
    caught up: the first wake-up after a restore is long, and what the generator makes
    meanwhile must not be counted as the measured run's lag."""
    first, _, later = worker_log.partition("wake-up:")
    if first == worker_log:
        return False
    if not feed:
        return True
    lines = FEED_LINE.findall(later)
    return bool(lines) and float(lines[-1][3]) < SETTLED_LAG


def settle(since: str, timeout: float, feed: bool) -> float:
    """Wait until the worker has settled after `since`; returns the wait."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        out = subprocess.run(
            [*LT, "logs", "--since", since, "lt-worker"], capture_output=True, text=True
        ).stdout
        if settled(out, feed):
            return time.monotonic() - t0
        time.sleep(10)
    raise SystemExit(f"loadtest: the worker did not settle in {timeout:.0f} s")


def feed_of(scenario: str) -> tuple[float, int]:
    """(daily average in records/s, the shape's hour) of the scenario's usage feed: off in
    smoke, the night rate in billing-night, the peak everywhere else (1x is the peak)."""
    if scenario == "smoke":
        return 0.0, -1
    return FEED_RATE, NIGHT_HOUR if scenario == "billing-night" else PEAK_HOUR


def mcp_tokens(scenario: str, mult: float, steps: list[float]) -> int:
    """How many service tokens keep every principal under bss-mcp's rate limit."""
    if scenario == "stress":
        top = max(steps)
    elif scenario == "billing-night" and mult == 1:
        top = 0.2
    else:
        top = mult
    return max(1, math.ceil(MCP_RATE * top * 60 / MCP_PER_TOKEN))


def billing_date(last: str, today: date) -> date:
    """The date billing-night catches up: the first unbilled one. The dataset's dates are
    frozen, so this is the day after its last run, not the calendar's today; the worker
    leaves such a gap to the operator, and one catch-up of one date is one daily run."""
    if not last:
        raise ValueError("billing-night needs a dataset with at least one daily run")
    run_date = date.fromisoformat(last) + timedelta(days=1)
    if run_date >= today:
        raise ValueError(
            f"billing-night needs an unbilled date before today; the last run is {last}"
        )
    return run_date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--mult", default="1")
    parser.add_argument("--duration", default="")
    parser.add_argument("--tz", default="Europe/Moscow")
    parser.add_argument("--settle-timeout", type=float, default=3 * 3600)
    # debugging only (not for recorded results): keep the running SUT, skip the settle wait,
    # and print each failed check's response
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not SECRET.is_file() or not SECRET.stat().st_size:
        print("loadtest: run make loadtest-secret first", file=sys.stderr)
        return 2
    password = SECRET.read_text().strip()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    name = f"{args.scenario}-{stamp}"
    rundir = OUT / name
    rundir.mkdir(parents=True)
    tz = ZoneInfo(args.tz)
    run_date = None
    if args.scenario == "billing-night":
        last = psql("SELECT max(run_date) FROM billing_run WHERE kind = 'daily'").strip()
        try:
            run_date = billing_date(last, datetime.now(tz).date())
        except ValueError as error:
            print(f"loadtest: {error}", file=sys.stderr)
            return 2

    env = {
        **os.environ,
        "LOADTEST_WORKER_FAKE_NOW": "",
        "LOADTEST_UID": str(os.getuid()),
        "LOADTEST_GID": str(os.getgid()),
    }
    for key in PROD_SHAPED:
        env.pop(key, None)
    if args.scenario == "prod-shaped":
        env.update(PROD_SHAPED)
    rate, hour = feed_of(args.scenario)
    env["LOADTEST_STUB_USAGE_RATE"] = str(rate)
    env["LOADTEST_STUB_USAGE_HOUR"] = str(hour)
    metrics_token = secrets.token_urlsafe(24)
    env["LOADTEST_METRICS_TOKEN"] = metrics_token

    log(f"{name}: starting the SUT (feed {rate} records/s average, the shape's hour {hour})")
    since = datetime.now(UTC).isoformat(timespec="seconds")
    # a fresh lt-app and lt-worker (new token, rate): memory starts from zero in each run
    settled_after = 0.0
    if not args.reuse:
        subprocess.run([*LT, "up", "-d", "--wait", "lt-db"], env=env, check=True)
        subprocess.run(
            [*LT, "up", "-d", "--wait", "--force-recreate", "lt-app", "lt-worker", "lt-mcp"],
            env=env,
            check=True,
        )
        log("waiting for the worker to settle (its first wake-up, the feed caught up)")
        settled_after = settle(since, args.settle_timeout, bool(rate))
        log(f"worker settled after {settled_after:.0f} s")

    ctx = context(env)
    (rundir / "context.json").write_text(json.dumps(ctx, indent=1))
    (rundir / "data.json").write_text(psql(Path("loadtest/k6-data.sql").read_text()).strip())

    steps = [float(s) for s in os.environ.get("STEPS", "1,2,3,5").split(",")]
    tokens, token_ids = [], []
    for i in range(mcp_tokens(args.scenario, float(args.mult), steps)):
        token_out = subprocess.run(
            [*LT, "exec", "-T", "lt-mcp", "python", "-m", "bss_mcp.tokens", "create",
             f"lt-k6-{stamp.lower()}-{i}", "--scope", "read"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        tokens.append(token_out.stdout.splitlines()[0].strip())
        token_ids.append(re.search(r"id (\S+)", token_out.stderr).group(1))

    billing: dict = {}
    k6_env = {
        **env,
        "LOADTEST_OPERATOR_PASSWORD": password,
        "LOADTEST_MCP_TOKENS": ",".join(tokens),
    }
    extra = []
    if args.scenario == "billing-night":
        extra = ["-e", f"CATCHUP_DATE={run_date}", "--out", f"csv=/out/{name}/k6.csv.gz"]
        billing["run_date"] = str(run_date)

    psql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements; SELECT pg_stat_statements_reset();")
    ids = {s: container(s) for s in SUT}
    stats = Stats(rundir / "stats.jsonl", ids)
    stats.start()
    scrape_name = f"lt-scrape-{stamp}"
    subprocess.run(
        [*LT, "run", "-d", "--rm", "--no-deps", "--name", scrape_name, "lt-scrape",
         "python", "-m", "loadtest.scrape", name],
        env=env, check=True, capture_output=True,
    )  # fmt: skip
    stop = threading.Event()
    watcher = None
    if run_date is not None:
        watcher = threading.Thread(
            target=billing_watch, args=(run_date, billing, stop), daemon=True
        )
        watcher.start()

    cmd = [
        *LT, "run", "--rm", "--no-deps", "-T", "lt-k6", "run", "--quiet",
        "-e", f"SCENARIO={args.scenario}", "-e", f"RUN={name}", "-e", f"MULT={args.mult}",
        "-e", f"RUN_TAG={int(stamp[-6:])}",
        *(["-e", "VERBOSE=1"] if args.verbose else []),
        *(["-e", f"DURATION={args.duration}"] if args.duration else []),
        *extra, "/scripts/main.js",
    ]  # fmt: skip
    log(f"k6 {args.scenario} x{args.mult} started")
    t0 = time.monotonic()
    started = datetime.now(UTC)
    with (rundir / "k6.log").open("w") as k6log:
        k6 = subprocess.run(cmd, env=k6_env, stdout=k6log, stderr=subprocess.STDOUT, check=False)
    seconds = time.monotonic() - t0
    ended = datetime.now(UTC)
    log(f"k6 ended with {k6.returncode} after {seconds:.0f} s")
    if watcher is not None:
        if "run_seconds" in billing and "dump_seconds" not in billing:
            watcher.join(timeout=3600)  # the dump slot may come after k6's end
        stop.set()
    stats.stop.set()
    subprocess.run(["docker", "stop", scrape_name], capture_output=True, check=False)
    for token_id in token_ids:
        subprocess.run(
            [*LT, "exec", "-T", "lt-mcp", "python", "-m", "bss_mcp.tokens", "revoke", token_id],
            capture_output=True, check=False,
        )  # fmt: skip

    pg_stats = psql(Path("loadtest/pg-stats.sql").read_text())
    (rundir / "pg-stats.txt").write_text(pg_stats)
    worker_log = subprocess.run(
        [*LT, "logs", "--since", since, "lt-worker"], capture_output=True, text=True
    ).stdout
    app_log = subprocess.run(
        [*LT, "logs", "--since", since, "lt-app"], capture_output=True, text=True
    ).stdout
    (rundir / "worker.log").write_text(worker_log)
    (rundir / "app.log").write_text(app_log)

    summary_path = rundir / "k6-summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"metrics": {}}
    report: dict = {
        "scenario": args.scenario,
        "mult": args.mult,
        "started": started.isoformat(timespec="seconds"),
        "ended": ended.isoformat(timespec="seconds"),
        "seconds": round(seconds),
        "worker_settle_seconds": round(settled_after),
        "feed_rate": rate,
        "feed_hour": hour,
        "mcp_tokens": len(tokens),
        "k6_exit": k6.returncode,
        "context": ctx,
        "groups": groups_of(summary, seconds),
        "containers": container_stats(rundir / "stats.jsonl"),
        "feed": feed_lag(worker_log, started),
        "pool_timeouts": len(re.findall(r"PoolTimeout|couldn't get a connection", app_log)),
        "pg_stats": pg_stats,
    }
    reqs = metric(summary, "http_reqs") or {}
    failed = metric(summary, "http_req_failed") or {}
    checks = {
        "thresholds (k6 exit 0)": k6.returncode == 0,
        "k6 summary collected": summary_path.exists(),
        "requests": int(reqs.get("count", 0)),
        "error_rate": round(failed.get("rate", 0.0), 5),
        "all endpoint groups measured": set(report["groups"]) == set(GROUPS),
        "no dropped iterations": (metric(summary, "dropped_iterations") or {}).get("count", 0) == 0,
    }
    if rate and run_date is None:
        # billing-night only reports the lag: the feed waits while the worker bills
        lag = report["feed"]["max_lag_seconds"]
        checks["feed lag < 5 min"] = lag is not None and lag < LAG_LIMIT
    if args.scenario == "soak":
        growth = {c: r["mem_growth"] for c, r in report["containers"].items()}
        checks["RSS growth <= 20 %"] = all((g or 0) <= RSS_GROWTH for g in growth.values())
        checks["no pool timeouts"] = report["pool_timeouts"] == 0
    if args.scenario == "stress":
        steps = {}
        for s in os.environ.get("STEPS", "1,2,3,5").split(","):
            steps[s] = groups_of(summary, 0, step=s)
        report["steps"] = steps
        broken = [(s, g) for s, gs in steps.items() for g, r in gs.items() if not r["pass"]]
        report["first_break"] = broken[0] if broken else None
    if run_date is not None:
        if billing.get("run_started") and billing.get("run_finished"):
            billing["latency_during_run"] = window_latency(
                rundir / "k6.csv.gz", billing["run_started"], billing["run_finished"]
            )
        report["billing"] = billing
        checks["run < 90 min"] = (billing.get("run_seconds") or 1e9) < RUN_LIMIT
    report["checks"] = checks
    if args.scenario == "stress" or args.scenario == "prod-shaped":
        report["pass"] = None  # a ceiling to find, not a verdict
    else:
        report["pass"] = all(v for v in checks.values() if isinstance(v, bool))
    (rundir / "report.json").write_text(json.dumps(report, indent=1))
    write_markdown(rundir / "report.md", report)

    leaked = [
        p.name
        for p in rundir.iterdir()
        if p.is_file()
        and p.suffix != ".gz"
        and any(s in p.read_text(errors="ignore") for s in (password, *tokens, metrics_token))
    ]
    if leaked:
        for p in leaked:
            (rundir / p).unlink()
        print(f"loadtest: a secret was in {leaked}; those files were deleted", file=sys.stderr)
        return 3
    log(f"report: {rundir / 'report.md'}  verdict: {report['pass']}")
    return 0 if report["pass"] is not False else 1


if __name__ == "__main__":
    sys.exit(main())
