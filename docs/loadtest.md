# Load test: the local stack, the dataset and the k6 runbook

The load-test programme (#281-#289) runs entirely on the workstation (changed by the human
on 2026-11-09; no VPS is involved). This page covers the stack and the dataset generator
(#289), and the k6 scenarios with their runbook (#281, [below](#the-k6-scenarios-281)).
The recorded results are in [loadtest-results.md](loadtest-results.md).

## The stack

`compose.loadtest.yaml` adds its own services next to the dev stack of the task worktree.
They are applied only by `scripts/task-compose --loadtest`, which the `make loadtest-*`
targets use:

| Service | What | Limits |
|---|---|---|
| `lt-db` | postgres 16, `max_connections=80`, `shared_buffers=1GB` (2GB did not fit the limit: [results](loadtest-results.md)), `pg_stat_statements`, fsync on, its own volume `lt_pgdata` | 3g |
| `lt-app` | the runtime image (no reload), `APP_ENV=test` | 3.0 CPU, 1536m |
| `lt-worker` | the billing worker, same image | 3.0 CPU, 1024m |
| `lt-mcp` | the MCP connector, same image | 0.5 CPU, 256m |
| `lt-tools` | one-off commands (migrations, the generator), the dev image with the sources mounted read-only; writes only `loadtest/out/` | not pinned |
| `lt-k6` | k6 1.3.0, the official image pinned by digest (Grafana's GHCR copy), started per scenario by `make loadtest` | `LOADTEST_K6_CPUS` (default `8-9`), 2g |
| `lt-scrape` | scrapes lt-app's `/metrics` every 15 s during a scenario (`loadtest/scrape.py`) | `LOADTEST_K6_CPUS`, 256m |

The system under test (`lt-db`, `lt-app`, `lt-worker`, `lt-mcp`) shares one cpuset,
`LOADTEST_SUT_CPUS`, which defaults to `4-7`: 4 logical CPUs, both threads of cores 2 and
3 (`lscpu -e`), emulating a 4-core host. k6 and the scraper get `LOADTEST_K6_CPUS`,
which defaults to `8-9` (both threads of core 4), a 2 vCPU load generator that does not
overlap the SUT. CPUs 0-3 and 10-11 stay free for WSL and the other containers. Set either variable in the
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
| `make loadtest-growth` | rows and bytes per table (a partitioned table with its partitions) and rows per month of the growing tables (#282); also saved to `loadtest/out/` |
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

## The k6 scenarios (#281)

`make loadtest SCENARIO=<name> [MULT=1] [DURATION=...]` runs one scenario
(`loadtest/run.py` on the host, `loadtest/k6/main.js` in `lt-k6`). The target is always
this task's load-test stack. A run writes `loadtest/out/<scenario>-<time>/` (ignored by
git): `report.md` and `report.json` (the verdict), `k6-summary.json`, `context.json`
(`lscpu -e`, the cpusets and limits, the host's other containers with their CPU %),
`stats.jsonl` (`docker stats` every 10 s), `metrics.prom` (`/metrics` every 15 s),
`pg-stats.txt` (the top 20 statements of the run), `worker.log`, `app.log`, `k6.log`
and `data.json` (the ids k6 sampled; `loadtest/k6-data.sql`).

| Scenario | What | Time |
|---|---|---|
| `smoke` | 1 VU, every request type once, all must pass; feed off | 1 min |
| `steady` | the 1x mix for 30 min, the feed at the peak hour's 70 records/s | 30 min |
| `stress` | 1x, 2x, 3x, 5x for 10 min each; the report names the first step and group that break a target (`first_break`) | 42 min |
| `soak` | 1x for 3 h; flags RSS growth > 20 % per container and any pool timeout | 3 h |
| `billing-night` | 20 % of the mix, the feed at the night's 8 records/s, one catch-up run of the first unbilled date through `POST /internal/v1/billingRun/catchUp`; measures the run, the API latency while it runs, and a `pg_dump -Fc` started 105 min after the run start | 2 h |
| `prod-shaped` | steady at today's production limits (app 0.5 CPU / 384m / 1 process / pool 10, worker 0.25 CPU, postgres 512m, `shared_buffers` 128MB); its numbers are the current ceiling, not a verdict | 30 min |

**The mix at 1x** (operations per second; `MULT` scales every rate and the UI VUs; open
model, `constant-arrival-rate`):

| Group | Rate | Requests |
|---|---|---|
| `read` | 25/s | one customer, billing account, product, bucket (balance) or bill, by ids sampled from the dataset |
| `list` | 5/s (inside the read budget of 30/s) | the first page or a random offset up to 5000 of customers, billing accounts, products, bills; productUsage of a product |
| `write` | 5/s | 40 % top-up, 25 % payment, 20 % new subscriber (party, customer, postpaid account, number, order: 5 requests), 10 % plan change, 5 % termination. Changes and terminations are postpaid lines from disjoint pools, dated tomorrow noon |
| `ui` | 20 VUs `lt-op-NN` | the account card (the account, then its panels in parallel) or the accounts list, 5-15 s think time |
| `mcp` | 2/s | `get_*` tool calls through lt-mcp, spread over throwaway read-only static tokens: bss-mcp allows a principal 60 calls a minute, as in production, so the runner makes enough tokens to keep each under 40 (3 at 1x, 15 for stress) |
| `usage-post` | 5/s | `POST /productUsage` |
| `auth` | once per VU | the sign-in (`POST /internal/v1/session`) |

API VUs reuse 20 operator sessions created sequentially in setup; UI VUs sign in once
as `lt-op-NN`, staggered, with their own `X-Forwarded-For`. Cookies persist between
iterations. Signing in every API VU at once previously exhausted the app's memory
during password hashing. The load-test app
trusts that header (`APP_TRUST_FORWARDED=true`, as behind Caddy in production), so the
sign-in throttle of 20 a minute per address sees operators, not the one k6 container.

**Pass criteria** (k6 thresholds; a missed one makes k6 exit 99 and the run fail): per
group p95 < 1 s and p99 < 1.5 s; < 0.1 % failed requests overall and per group. The
runner adds: the feed lag (the age of the oldest unacknowledged stub record, from the
worker's `stub usage load:` lines) < 5 min, except in billing-night, where it is only
reported (the feed waits while the worker bills); billing-night: the run < 90 min; soak: no
container's memory grows more than 20 % from the first to the last tenth of the run, and
no pool timeout in the app log.

**The usage feed.** `LOADTEST_STUB_USAGE_RATE` (set by the runner) becomes the worker's
`BSS_STUB_USAGE_RATE`: the stub network produces records at that daily average with a
daily shape (70/s at 20:00, 8/s at 04:00 at 23/s), each for an MSISDN assigned that day,
and the worker's existing feed step stores them
(`app/integrations/network/usage_load.py`). It is refused unless `APP_ENV=test`. 1x is
the peak load, so the runner pins the generator to one hour of the shape
(`LOADTEST_STUB_USAGE_HOUR`, the worker's `BSS_STUB_USAGE_HOUR`): hour 20 (70/s) in
steady, stress, soak and prod-shaped, hour 4 (8/s) in billing-night, at any time of day.
The feed step takes batches of 500 until the feed is empty or 120 s have passed
([usage.md](decisions/usage.md#feed)).

**Credentials** come only from the environment: the operators' password from
`~/.config/bss/loadtest/operator-password` (`make loadtest-secret`), a fresh `/metrics`
token per run, and read-only MCP tokens created before k6 and revoked after it. None is
written to `loadtest/out/`; the runner checks the run's files for them and deletes any
file that holds one.

**`APP_ENV=test` inventory.** The worker clock override (`app/worker/runtime.py`) is
refused outside test; the runner unsets it, so billing uses the real clock. The new
usage generator checks test in `usage_load.py`, called by the adapter and worker
startup. Existing environment-sensitive request behavior is the non-Secure local HTTP
session cookie (`app/internal/users.py`) and permission for HTTP/private callback URLs
(`app/events/callbacks.py`, `LOCAL_ENVS`). No hub/callback operation is in this mix.
`app/internal/context.py` reports the environment as metadata. Thus test does not alter
the measured billing rules or TMF operations, but it does change cookie transport and
callback validation; a blanket claim of identical request behavior would be incorrect.

### Runbook

The agent runs all of it; long scenarios in the background.

1. `make loadtest-secret` (once) and `make loadtest-up`.
2. `make loadtest-restore NAME=seed100k` before every measured scenario: each one writes
   to the database. The restore takes about 30 min (the load, the migrations, `ANALYZE`).
3. `make loadtest SCENARIO=smoke` must pass first: it proves every request type against
   the restored data.
4. `make loadtest SCENARIO=<name>` for steady, stress, soak, billing-night and
   prod-shaped, a restore before each. The runner recreates lt-app, lt-worker and lt-mcp
   with the scenario's settings and waits for the worker's first wake-up to end before k6
   starts (the dataset is days old, so the first wake-up catches up on prepaid work).
5. Billing-night catches up the dataset's first unbilled date (5 November on `seed100k`):
   one date, so one daily run of all 100k accounts. The worker does not bill that gap by
   itself, so the run starts only when k6 asks for it, 30 s into the scenario.
6. To prove the thresholds: `make loadtest SCENARIO=steady MULT=20 DURATION=3m` must fail
   (k6 exit 99).

Dropped iterations also fail the k6 thresholds: latency alone does not prove that the
generator delivered the requested arrival rate. An interrupted k6 process without a
summary produces a failed report with missing groups, rather than a successful empty
report. Each ID pool is a separate `SharedArray`, so accessing one ID does not copy the
entire exported dataset into each VU.

What to watch while it runs: `make loadtest-ps` (health), `make loadtest-logs` (the app's
errors and the worker's `stub usage load:` lines), `tail -f loadtest/out/<run>/stats.jsonl`.
After it: `report.md`, then fill in [loadtest-results.md](loadtest-results.md).

## SQL plan sweep (#327)

Measured coverage, limitations and follow-up findings: [results](loadtest-plan-sweep.md).

`make loadtest-plan-sweep` collects application statement shapes from the full test
suite and runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` against the restored 100k
dataset. This complements k6: it includes SQL reached by the suite outside its HTTP mix.

1. `make up` builds the local development image.
2. `make loadtest-restore NAME=seed100k` restores, migrates and analyzes the task's own
   load-test database. Other tasks' databases are never used.
3. `make loadtest-plan-sweep` collects the full suite, exports server statistics, stops
   this task's load-test app/worker/MCP and measures on `lt-db` with four account profiles:
   median-id account, most lines, prepaid and postpaid.

Output is under `loadtest/out/plan-sweep-<UTC timestamp>/`: per-process attribution,
`shapes.json` (server query IDs, normalized shapes, calls, source lines and tests),
`report.json` (full plans and every profile's binding provenance/errors), and
`report.md` sorted by worst execution time. Fixture-only, utility and unattributed
server statements are retained separately with an exclusion reason. Query IDs from
cloned test databases are grouped by a literal-independent SQL fingerprint; query IDs
are not assumed stable across databases or PostgreSQL versions.

For iteration, `make loadtest-plan-sweep PHASE=collect` collects only, and
`make loadtest-plan-sweep PHASE=measure SWEEP=plan-sweep-<timestamp>` reuses the shapes.
`PHASE=export` rebuilds the shape export from the completed attribution and still-live
test-server statistics. Repeated measurement attempts archive the previous reports.
`PHASE=prepare` only resets statistics and creates the run directory; it prints the
instrumented `make test` command. The same capture variables can be passed to
`make commit` when its normal hook already selects the full suite (for example, a
Compose change): the hook's tests and selection remain unchanged. Export after that
hook finishes, then measure. This records the exact committed snapshot without an
extra full-suite run solely for attribution.
A collection run increases the test server's statistics capacity to avoid eviction;
ordinary tests use its default 10k slots. A report with evictions or coverage below
90% fails the command. Failed/unsupported shapes remain **unbound**, with the error;
partial failures of the four account profiles remain visible and are not hidden.
The coverage report separately counts all-four-profile and partial-profile successes.
Nullable values stay nullable; the binder does not invent a foreign key for a NULL.
The JSON separately counts shapes using typed UUID proxies from a sampled account when
a feature table is empty or its target cannot be resolved. Those plans cover empty/
missing-row paths, not performance with a populated feature table. Every such parameter
is marked in its binding provenance; this limitation is separate from successful SQL
execution coverage. Relation-size estimates are included for interpreting sparse data.
Run-ID samples prefer the latest daily run, honoring an explicit prepaid-command kind,
so daily bookkeeping is exercised with a daily run rather than a command run's ID.

Flags: a sequential scan of a relation with over 10k estimated rows, execution over
5 ms on per-request/account paths (conservative default), over 100 ms on recognized
once-per-tick/run functions, or a row estimate off by more than 100x. Inspect both
the flag and the plan: an unfiltered bulk operation can legitimately scan a large table.
Estimate comparisons skip unexecuted or partially consumed nodes (for example an
index scan stopped by `LIMIT`); blocking sorts/hash builds still consume and check
their full inputs. The excerpt records whether an estimate comparison is meaningful.
Known findings are marked `known (#310)` or `known (#311)`; the sweep fixes no queries.

Every analyzed statement runs in its own transaction and is rolled back. Sequence
values are restored after the offline sweep, because PostgreSQL sequences do not roll
back with a transaction. Session-lock and external-effect functions are unbound.
Sequence restoration is verified, with a database-identity-bound recovery journal if
the process is interrupted. The account count is checked before and after measurement.
Statement and lock timeouts bound each attempt. No constraint/trigger is disabled,
and a failed INSERT is never counted as a successful analyzed write. These repeated,
mostly warm-cache plans are diagnostic timings, not concurrent-load capacity results.
Deferred constraint triggers that fire only at commit are outside a rolled-back
statement's execution time; these plans do not replace commit-time or end-to-end tests.
