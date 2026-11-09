# Billing worker (stage 4)

The [refund/bill-delivery milestone](billing-completion.md) adds durable on-demand
requests after the ordinary request queue, and JSON document snapshots at bill
issue. Both paths keep the billing ownership lock and account row locks. Monthly
runs exclude offCycle documents when finding cycle anchors; settlement excludes
credit consumed by cash payouts.

Index: [README.md](README.md). The design note is
[docs/archive/stage4-design.md](../archive/stage4-design.md); operations: [docs/billing-worker.md](../billing-worker.md).

## Billing worker (stage 4, in progress)

`app/worker/`, from `docs/archive/stage4-design.md` and the stage 4 planning decisions (Q1-Q14,
recorded below). What exists so far (slice b: b1, b2, b3; slices c, h, d, e):

- **Ownership** (`db.py`): one connection per worker (W1: autocommit, `connect_timeout`
  10, keepalives 30/10/3, `lock_timeout` 30s); the session advisory lock on it says who
  owns billing - the lock and the work fail together.
- **`start_run`** (`runs.py`): ownership first (`RunInProgress` otherwise); then new /
  `AlreadyCompleted` / reopened (failed, attempt + 1) / taken over (running, owner gone).
- **One date, account by account** (`process.py`, `DateRun.step()`): members = accounts
  opened before 00:00 of the date (`opened_at`, injected clock), in id order. Per account
  **one transaction** - its charges and its `billing_run_account` row commit together
  (proven: a failure between them leaves neither, and a takeover bills it exactly once):
  an unresolved earlier failure -> `blocked` (waits for that run); `BillingError` ->
  `failed`; else `ok`. Accounts already ok in the run are never processed again.
- **"Completed" means every member is ok**: `billing.finish_run` refuses (`RunNotComplete`)
  while a member is failed, blocked or has no row; otherwise the run closes `failed` with
  `failure_kind=accounts` ("N of M accounts not billed (...)") and later dates go ahead.
- **Retry** (`retry_run`): only the run's failed accounts; an account that succeeds is
  then billed for its blocked later dates in order, reopening one run at a time (Q2), and
  stops at the next failure (later blocked rows then wait for that run). Proven against
  a control database: the account ends with exactly the charges and bills of a run where
  nothing failed (a charge on Dec 1 and a refund on Dec 3, not one reduced charge).
- **Whole-run failure** (slice b3): anything but an account's `BillingError` - the
  database gone, `LockNotAvailable`, a bug - aborts the run (`RunAborted`). If the
  connection still works the run is marked `failed`, `failure_kind=run`; a dead worker
  cannot mark it, so it stays `running` and its lock dies with the connection (takeover).
  **No later date is billed** while a run failed as a whole (`HeldBack`), and the
  scheduled retry of that date waits **5 min, 15 min, then hourly** from `finished_at`
  (`NotYet`, `plan.retry_time`); an operator's retry or catch-up is not held to the
  backoff. Accounts already ok are never billed again.
- **`LockNotAvailable` is necessarily run-level**: an account's outcome row has a foreign
  key to its `billing_account` row, so recording any outcome for a locked account needs
  the same lock - proven when a mutation that classified it as an account failure still
  aborted the run. The negative proof that counts is "skip the locked account and go on",
  which the test catches.
- **Takeover**: after a death between accounts or a dropped connection the successor's
  `start_run` takes the run over (attempt + 1, `started_at` = now: the attempt's start, as
  on a reopen) and bills only the accounts without an ok row; while the owner is alive a
  rival gets `RunInProgress`.
- **Status flips** (slice c, `products.flip_statuses`, run by `bill_date` at the start of
  each run, in its own transaction, before billing, inside the whole-run failure
  handling): `created`/`pendingActive` -> `active` once `start_date` <= run date,
  `pendingTerminate` -> `terminated` once `termination_date` <= run date, only for
  products whose order (creating, resp. terminating) completed before 00:00 of the run
  date; blocked accounts included (statuses follow dates, D-W5); every other status
  untouched; idempotent. The due products are locked `ORDER BY id FOR UPDATE` first,
  like `lock_products` in ordering, so a delete order and the flips only wait for each
  other (Q8) - with mismatched orders the test gets a real `DeadlockDetected`. The
  visibility cut-off cannot decide anything for API-made data (a product due by D whose
  order completed on D starts on D and is active at once), so it is tested with orders
  completed at 23:59:59 and 00:00:00.
  Review C (2026-10-19): a `created` product can already have a scheduled end. At its
  start it becomes `pendingTerminate`, or `terminated` when a catch-up run has reached
  the end too. The terminating order must also be visible before the run's cutoff;
  an end placed after that cutoff does not affect that historical flip.
- **The request queue** (slice h, `queue.process_requests`): the internal API only inserts
  `worker_request` rows; the owner takes them oldest first (marks picked, carries out,
  marks done). catchUp runs `plan.catch_up_dates` in order with the `catchUp` trigger
  (stops at a whole-run failure); retry: `retry_run` for an accounts failure, the date
  again (trigger `retry`, no backoff) for a whole-run failure, nothing if no longer
  failed. A request that raises is still marked done (logged) so the queue cannot
  block; a picked request of a dead worker is picked again. **Ties of `requested_at`
  are broken by `created_at`** (insertion order): ordering by the random id carried out
  two requests of the same instant in random order - 14 of 20 runs of the test failed
  before the fix, 20 of 20 passed after.
- **The schedule** (slice d, `plan.scheduled`, `loop.tick`): a date is due at
  `BILLING_RUN_AT` (00:30), not 00:00 (Q5); before that only up to yesterday. A wake-up
  bills on its own: a run that failed as a whole (its date only, after the backoff); a
  run left `running` (its worker was killed or stopped - the 03:00 reboot, a deploy) is
  resumed first and taken over; then every date after the latest run up to the due date
  **if they lie in the automatic window (the due date and the day before, Q4)** - an older
  missing date is a gap: nothing is billed, a warning says "catch up from the admin UI"
  (billing past it would leave it impossible to replay in order); first start ever: the
  due date only (D-W15). Before a new scheduled date the runs that failed because of
  accounts are retried once (D-W17); then the dates; then the queue.
- **The process** (`python -m app.worker`, `--once` for one wake-up): refuses to start
  (exit 2) unless `BILLING_RUN_AT` is strictly between 00:00 and 02:00 (D-W13) and unless
  `WORKER_FAKE_NOW` is unset or `APP_ENV=test` (Q14). Wakes every 60 s; SIGTERM/SIGINT
  finish the account in hand, leave the run `running` (not failed), close the connection
  (the lock goes with it) and exit 0 (D-W11); SIGKILL leaves nothing half-written and the
  next worker takes the run over. The liveness file (`WORKER_LIVENESS_FILE`, default
  `/tmp/worker-alive`) is touched every wake-up and after every account of every path -
  scheduled, catch-up, and the retries, which have no stop checkpoint - through the
  heartbeat (`DateRun(on_account=...)`, called in `step()`; `0d463e0`), for slice f's
  healthcheck. `tests/test_worker_heartbeat.py` proves it scaled in time (threshold 1 s,
  0.3 s per account, every run longer than the threshold): one heartbeat per account and
  no gap over the threshold in all four paths; without the call all four fail. Proven on real processes on a pinned clock (`tests/test_worker_signals.py`).
  **Found by the SIGKILL test:** the first planner only looked at dates after the latest
  run, so a run left `running` was never resumed - fixed by resuming the earliest
  unfinished run first.
- **`GET /billing-status`** (slice e, `app/status.py`): the external monitor's freshness
  signal, unauthenticated (a Caddy carve-out like `/healthz`), so the body is exactly
  `{date, status, ageSeconds}` of the latest run - no amounts, names or counts (a strict
  schema with `additionalProperties: false` checks every response in the tests). The
  expected date is today once the local time (BILLING_TZ) is past `BILLING_RUN_AT` + 2 h
  grace, else yesterday (D-W19). Latest run: failed -> 503 `failed`; running -> 200
  `running` for under 2 h since `started_at` (a takeover restarts it), 503 after or once
  its date is before the expected date (a worker that keeps dying cannot hold 200);
  completed on or after the expected date -> 200 `ok`,
  earlier -> 503 `stale`; no run at all -> 503 `{"date": null, "status": "none",
  "ageSeconds": null}` (Q6). `ageSeconds` counts from `finished_at`, else `started_at`.
  **Not part of `/healthz`**: a late run never marks the app unhealthy (tested). Not in
  any spec; an unknown query parameter is a 400. Negative proofs: failed read as
  completed, running never overdue, grace removed, an extra body field.
  **By design, it is 503 while the worker catches up an older date after downtime**
  (503 `running` on a late date, 503 `stale` between dates) until the run of the
  expected date: the external monitor alerts during every catch-up - billing is behind.
  The deploy's check and `make smoke` accept 503 `running`/`stale` (shape only), so they
  do not fail on it (`docs/billing-worker.md`, "Billing status").
- **Test pitfalls found here**: `pg_locks` is cluster-wide - count advisory locks of the
  current database only (parallel tests hold their own); `pg_terminate_backend(pid)`
  returns before the backend exits - use `pg_terminate_backend(pid, 5000)`.
- **The seed** bills each day through `bill_date` on a worker connection; every seeded run
  is completed with one ok row per member.
- **Deployment** (slice f): `worker` in `compose.prod.yaml` only - same image,
  `python -m app.worker`, `restart: unless-stopped`, `stop_grace_period: 60s` (D-W11),
  256m / 0.25 CPU, read-only with `/tmp` tmpfs, no ports or alias, the app's environment
  (`APP_ENV`, `LOG_LEVEL`, `DATABASE_URL`; `BILLING_TZ`/`BILLING_RUN_AT` passed to neither,
  so both use the same defaults). **Healthcheck (Q13)** replaces the image's `/healthz`
  probe: `/tmp/worker-alive` younger than 180 s; a worker that cannot reach the database
  stops touching it and goes unhealthy. It is `find -mmin -3` in bash, no Python (see
  "Healthchecks" under [Conventions](infra.md#conventions)). `deploy.sh`
  waits for **both** `app` and `worker` healthy (90 s) and checks `/billing-status` on
  the VPS: 200 must say `ok` or `running` (a run younger than the 2 h grace - e.g. the
  one the deploy's own worker restart left for its successor to take over), 503 one of
  `stale|failed|running|none`, anything else fails (body printed - date, status, age
  only); `make smoke` checks the same locally (proven: without `stale` in the set it
  failed on the local 503 stale). Both call `scripts/billing-status-check.sh`; its cases
  (`scripts/test-billing-status-check.sh`, run by `make test`) cover every answer of
  `app/status.py` both ways - proven: with 200 = `ok` only (the earlier rule, doc finding
  #1) both 200 `running` cases fail, with any status on a 200 five reject cases fail. Locally there is no
  long-running worker: **`make worker-once`** (D-W14). **The first deploy starts billing
  on the VPS**: no runs yet -> the due date only (D-W15).

## Resolved questions

Moved from CLAUDE.md's old "Open questions".

- **Stale billing runs and `start_run` semantics: resolved in stage 4 slice b1.**
  `app/worker/runs.py` `start_run` takes ownership first (a session advisory lock on the
  worker's own connection, `app/worker/db.py`; another live owner -> `RunInProgress`
  before anything is read), then: no run -> new; completed -> `AlreadyCompleted` (a
  no-op); failed -> reopened (attempt + 1); running -> taken over (its owner is gone:
  the lock dies with the connection; attempt + 1, trigger kept). The repository only
  writes rows (`insert_run`, `lock_run`, `reopen_run`, `take_over_run`);
  `BillingRunConflict` is gone.
- **`APP_ENV` unset means `prod`** (`app/config.py`, since slice b1): the worker's clock
  override (`WORKER_FAKE_NOW`, Q14) needs an explicit `APP_ENV=test`. compose always sets
  it (`dev` locally); **`compose.prod.yaml` sets `staging`** for app and worker - decided
  in slice f (2026-10-05): `vps-test` is a verification host, so it stays `staging`.

## Account review (2026-11-09, #292)

The prepaid lifecycle (`process_lifecycle`), entitlement (`observe_entitlements`) and
low-balance/grace notification (`events._accounts`) passes locked and evaluated every
prepaid account on every tick. Measured on the load-test seed: about 9 ms per account per
pass, so at 70k prepaid accounts one pass alone would take about 10 minutes, on a worker
that wakes every minute. The design was approved by the human on 2026-11-09:

- **When an outcome can change.** The outcome of these passes changes only with the
  business date (grace, suspension, deactivation and plan expiry are date-granular) or with
  the account's money and products.
- **Change marks.** Triggers (migration 0068) mark a prepaid account in `account_review`
  whenever a row those passes read changes. The tables are: payments, balance actions and
  reversals, allocations, bills, ABRs, grants, holds and their sources, funding sources and
  consumptions, refunds, prepaid commands and shortfalls, usage charges and bars, products
  and their status changes, order items and offering changes, and the account row. They are
  triggers rather than calls in each write path, so no path can forget. Postpaid accounts
  are never marked.
- **Which accounts a tick evaluates.** Every prepaid account (a full sweep) on the first
  tick of a business date and after a change of the lifecycle settings or of
  `NOTIFY_LOW_BALANCE` / `NOTIFY_EXPIRING_DAYS`. Otherwise only the marked ones. The date
  and settings of the last sweep are one row in `account_review_sweep`. It lives in the
  database, so a restart needs no sweep of its own.
- **Clearing the marks.** Each mark carries a version from a sequence. After all three
  passes, the tick deletes the marks at the versions it read. A change made during the tick
  has a newer version and is evaluated on the next one, so "at most one tick later". A tick
  stopped by SIGTERM deletes nothing and records no sweep.
- **The passes keep their rules.** Only the set of accounts they look at changes.
  `accounts=None` (tests and direct calls) still means every account.
