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
| `make loadtest-dump NAME=...` / `make loadtest-restore NAME=...` | save the database to `~/.local/share/bss/loadtest/<NAME>.dump`, outside every worktree, or replace it from there and migrate. This is how another task's worktree gets the dataset |
| `make loadtest-pg-stats-reset` / `make loadtest-pg-stats` | reset `pg_stat_statements`; print the top 20 statements and save them to `loadtest/out/` |

## Measuring a billing run

`make loadtest-billing-run DATE=<run date> [CPUS=1]` (#283, `loadtest/billing_run.py`,
run on the host) measures the daily run of DATE on the restored dataset. The run of the
day before must exist and DATE must have no run. Restore first: the measurement writes
the run.

1. It resets `pg_stat_statements`.
2. It recreates `lt-worker` with `cpus` = CPUS (`LOADTEST_WORKER_CPUS`) and its clock
   pinned to DATE 00:31 (`LOADTEST_WORKER_FAKE_NOW`, i.e. `WORKER_FAKE_NOW`; the
   overlay's `APP_ENV=test` allows it). The worker bills DATE on its own.
3. Every 5 s it reads the accounts done and samples `docker stats` of `lt-worker` and
   `lt-db`. Wall times are the host's, since the worker's own timestamps come from the
   pinned clock.
4. When the run ends it stops `lt-worker`, whose wake-up would go on with the queue and
   the prepaid sweep. It writes `loadtest/out/billing-run-<time>.json` with the wall
   time, accounts/s, queries per account, the top 10 statements, the CPU split,
   `lscpu -e` and the digest of the run's results (`loadtest/billing-digest.sql`). Two
   runs of one date on the same dump must give the same digest.

The results are in [billing-worker.md](billing-worker.md#capacity).

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

**Profiling.** `SEED_ARGS="--stop-after 1 --profile loadtest/out/day.prof"` runs one more day and writes a cProfile of the main process. With `PROCESSES=1` that covers everything.

Each simulated day ends with `ANALYZE`. The tables grow from empty, and plans made on stale statistics, cached in the generator's long sessions, scanned whole tables. `make loadtest-restore` analyzes too, since `pg_restore` leaves no statistics.

**Report.** `loadtest/out/seed-report.json` holds the seconds per phase, the counts, the
rates (subscribers/s, billed accounts/s, usage records/s, rows/s), the database size and
the rows per table.

## Measured duration and disk use

Filled in from the runs on the workstation (`lt-db` pinned to `4-7`, generator not
pinned).

| Run | Duration | Database | Notes |
|---|---|---|---|
| Day 7 of the 100k seed (about 20k subscribers), PROCESSES=1, before / after #292's first fixes | 1,960 s / 1,474 s (tick 1,050 / 665 s, creation 824 / 731 s) | | full table scans gone; `balance_ledger_check` 4.26 / 1.40 ms; the rest is about 1.2M queries a day at about 0.6 ms round trip each |
| N=1000, DAYS=10, USAGE_DAYS=3, PROCESSES=4 | 9 min 6 s (billing 70 s, subscribers 65 s, ticks 162 s, usage 221 s) | 59 MB | 64.7 billed accounts/s, 15.3 subscribers/s, 282 usage records/s; the database used about half of its 4 CPUs: the generator's serial Python path is the limit |

## The 100k dataset (#293, finished 2026-11-10)

`~/.local/share/bss/loadtest/seed100k.dump` (554 MB, `pg_dump -Fc`) is the only copy,
on the workstation's WSL disk; the human decided against a second copy. It holds 31
simulated days, 5 October to 4 November 2026. The human chose 31 of the planned 35 days:
every subscriber is billed at least once and one daily run covers all 100k accounts.

| What | Count |
|---|---|
| Billing accounts (subscribers) | 100,000 (70 % prepaid) |
| Products (lines) | 114,978 |
| Usage records (days 29-31) | 6,781,880 |
| Daily billing runs | 31 |
| ABRs / bills (bill state changes) | 132,668 / 372,433 state changes |
| Database size | 4.2 GB |

Days 11-31 took 16.9 h (6 processes, generator not pinned, database on `4-7`). The phases
were billing 6.3 h, the evening tick 7.3 h, usage 2.4 h (3 days) and creation 0.9 h.
Days 1-10 took about 2.5 h more, partly before #292's fixes.
- **The billing run of day 31**, the first with all 100k accounts as members: 1,441 s,
  about 24 min, 69 accounts/s in one process.
- **Usage:** about 790-810 records/s over 6 processes; each record is one set of
  statements (#305).
- **The evening tick:** a full sweep of every prepaid account once per simulated day
  (account review only helps a worker that ticks every minute), plus the day's purchases
  and renewals.

### Keeping and using it

- **Restore before every measured run:** `make loadtest-restore NAME=seed100k` in the
  task's own load-test stack. It loads the dump, migrates it to the current head and
  analyzes it. A test writes to the database, so each measured run starts from the dump.
  The task's database volume is deleted by `make finish-task`; the dump is what stays.
- **Schema changes** reach the dataset through the migrations a restore applies. A change
  to the data mix itself means seeding again: about a day for 31 days.
- **Dates are frozen.** The last billing run is 4 November 2026, and the dataset ages one
  day per calendar day. The worker does not bill such a gap by itself (an operator's
  catch-up does, at about 50 min per day at 100k). Tests run on these dates; the
  billing-night scenario (#281) starts one explicit catch-up run.
- **More days** can be added later. `make loadtest-seed N=100000 DAYS=35 USAGE_DAYS=7` on a
  restored dump continues at day 32: the generator resumes, and its parameters must stay
  the same.
