# Load test: the local stack and the dataset

The load-test programme (#281-#289) runs entirely on the workstation (changed by the human
on 2026-11-09; no VPS is involved). This page covers the stack and the dataset generator
(#289). The k6 scenarios and their runbook are added by #281.

## The stack

`compose.loadtest.yaml` adds its own services next to the dev stack of the task worktree.
They are applied only by `scripts/task-compose --loadtest`, which the `make loadtest-*`
targets use:

| Service | What | Limits |
|---|---|---|
| `lt-db` | postgres 16, `max_connections=80`, `shared_buffers=2GB`, `pg_stat_statements`, fsync on, its own volume `lt_pgdata` | 3g |
| `lt-app` | the runtime image (no reload), `APP_ENV=test` | 3.0 CPU, 1536m |
| `lt-worker` | the billing worker, same image | 3.0 CPU, 1024m |
| `lt-mcp` | the MCP connector, same image | 0.5 CPU, 256m |
| `lt-tools` | one-off commands (migrations, the generator), the dev image with the sources mounted read-only; writes only `loadtest/out/` | not pinned |

The system under test (`lt-db`, `lt-app`, `lt-worker`, `lt-mcp`) shares one cpuset,
`LOADTEST_SUT_CPUS`, which defaults to `4-7`: 4 logical CPUs, both threads of cores 2 and
3 (`lscpu -e`), emulating a 4-core host. k6 will get `LOADTEST_K6_CPUS` (#281). CPUs 0-3
and 10-11 stay free for WSL and the other containers. Set either variable in the
environment of the `make` call to move a set, then recreate the containers
(`make loadtest-down loadtest-up`).

The services publish no host ports and use their own database, so `make up`, `make test`
and the pre-commit hook (all on the dev `db`) neither see nor disturb them. Plain
`task-compose` calls report the `lt-*` containers as orphans; that warning is harmless.

| Target | What it does |
|---|---|
| `make loadtest-up` | builds the images, starts `lt-db`, migrates it, starts the rest |
| `make loadtest-down` | stops and removes the `lt-*` containers; the database volume stays |
| `make loadtest-ps` / `make loadtest-logs` | status and the last 5 minutes of app and worker logs |
| `make loadtest-reset` | empties the database (drop, create, migrate) |
| `make loadtest-secret` | creates the operators' password file (once) |
| `make loadtest-seed N=...` | builds the dataset (below) |
| `make loadtest-pg-stats-reset` / `make loadtest-pg-stats` | reset `pg_stat_statements`; print the top 20 statements and save them to `loadtest/out/` |

## The dataset

`make loadtest-seed N=<subscribers> [PROCESSES=4] [DAYS=90] [USAGE_DAYS=30]` runs
`python -m loadtest.seed` in `lt-tools` against `lt-db`. It stops `lt-worker` first: the
generator owns billing while it runs. Start the worker again with `make loadtest-up`
afterwards.

**How it works.** The generator works like `make seed` (`tests/seed.py`), at scale. The
FastAPI app runs in process behind a FixedClock that moves forward. The worker's own
functions run in the same process on one worker connection with the same clock. Day 0 is
today - DAYS. Every day has these steps:

1. The billing run of the date at 00:30 (`bill_date`).
2. The day's new subscribers between 08:00 and 20:00. The first min(30, DAYS) days create
   all N, in `PROCESSES` parallel processes, each with its own app and connection pool.
3. At 20:00, the day's recurring top-ups, plan changes and terminations, and the payment
   of every bill dated 10 days earlier.
4. One worker tick at 23:00 (completes prepaid purchases, renewals, rating, and the rest of
   the worker's work).
5. In the last USAGE_DAYS days, at 23:30, 20 usage records per active line through the
   stub network's feed.

It ends with yesterday's run, so `lt-worker` carries on with today.

**Speed-ups (approved by the human 2026-11-09).** With `PROCESSES` > 1, each usage day is
split by line between the creation processes. Each one stores its lines' records with the
feed's own `ingest` (`app/network/usage.py`), on its own connection, in transactions of one
feed batch (500). With one process the records go through the stub network and the
worker's feed step, as the tests run it. A test checks that both give the same rows. While
the generator runs, the database is set to `synchronous_commit = off`
(`--no-fast-commit` turns that off). A crash then loses only the last commits, which the
rerun redoes. The setting is reset at the end, so the load tests run with the default.

**The 100k dataset** is `make loadtest-seed N=100000 DAYS=35 USAGE_DAYS=7`. The human
chose this shape on 2026-11-09 instead of 90 / 30 days: the approved shape was estimated at
about 145 h on the workstation. It gives 30 days of creation, 5 days with all 100k
subscribers, 35 daily runs and 7 days of usage.

**The mix** for subscriber `lt-<n>` is decided by `(seed, n)` alone:

- 70 % prepaid, 30 % postpaid; 15 % have a second line; each line is Mobile S (80 %) or
  Mobile L.
- Every account has a US service address. All postpaid accounts and half of the prepaid
  ones have a saved card.
- Prepaid accounts top up at activation; 60 % of them top up again every 30 days.
- 90 % of postpaid subscribers pay each bill 10 days after its date.
- 2 % of lines change plan and 1 % are terminated. A prepaid plan change is dated at the
  renewal, so it needs no funds held.
- Usage is 60 % data, 25 % voice and 15 % SMS.
- Numbers are `1302...`, clear of the stub network's test suffixes.
- The users are `lt-admin` (admin) and the operators `lt-op-01`..`lt-op-20` (role
  `user`), all with the password in `~/.config/bss/loadtest/operator-password`
  (`make loadtest-secret`). The file is outside every worktree, and the generator sets
  the password through the real first-sign-in password change.

**Resuming.** Each finished day is a row in `loadtest_progress`. The parameters are in
`loadtest_config`. Both tables are created by the generator, never by a migration. A
rerun with the same parameters continues at the first unfinished day and redoes it
without duplicates:

- parties, customers, accounts, numbers and cards are found by name before they are
  created;
- orders, payments and top-ups carry an Idempotency-Key;
- usage records have a fixed network id.

Other parameters, or a database holding data the generator did not create, are refused.
It refuses any `APP_ENV` but `test`.

**Report.** `loadtest/out/seed-report.json` holds the seconds per phase, the counts, the
rates (subscribers/s, billed accounts/s, usage records/s, rows/s), the database size and
the rows per table.

## Measured duration and disk use

Filled in from the runs on the workstation (`lt-db` pinned to `4-7`, generator not
pinned).

| Run | Duration | Database | Notes |
|---|---|---|---|
| N=1000, DAYS=10, USAGE_DAYS=3, PROCESSES=4 | 9 min 6 s (billing 70 s, subscribers 65 s, ticks 162 s, usage 221 s) | 59 MB | 64.7 billed accounts/s, 15.3 subscribers/s, 282 usage records/s; the database used about half of its 4 CPUs: the generator's serial Python path is the limit |
