# Stage 4 - the billing worker: design note (checkpoint 1, no code)

Status: proposal, written unattended. Every rule from stage 3 stays as it is (CLAUDE.md
"Billing (stage 3)"); this note decides who calls `bill_account`, when, and what happens
when it does not go to plan. Decisions that need the owner are marked **(D-Wn)** and
collected at the end with a recommendation each.

## 1. What the worker is

A second process from the **same image** as the app: `python -m app.worker`. No HTTP
server, no ports. It owns everything time-driven:

1. the daily billing run at **00:30 BILLING_TZ** (`BILLING_RUN_AT`, already in settings
   and shown by `/internal/v1/context`);
2. replay of missed dates, in order;
3. the product status flips (`created -> active`, `pendingTerminate -> terminated`);
4. manual requests from the UI (catch up to a date, retry the latest failed run).

The worker reads time only through the injected clock (`app/clock.py`); in tests it runs
on a `FixedClock`, and every decision below is a pure function of (clock, database
state) so it can be tested without sleeping.

## 2. Run lifecycle

The `billing_run` row per date exists since stage 1 (`run_date` UNIQUE, status
`running | completed | failed`, `started_at`, `finished_at`, `error`). Stage 4 makes
`start_run` answer every case instead of raising `BillingRunConflict` for two of them
(the open question in CLAUDE.md):

| Row for D | `start_run(D)` | Why |
|---|---|---|
| none | insert `running`, proceed | normal |
| `completed` | **`AlreadyCompleted` - a no-op**, not an error | replays, a second worker start, a manual catch-up overlapping the schedule |
| `failed` | reuse the row: back to `running`, `attempt + 1`, proceed | retry |
| `running`, **its owner is gone** | **take over**: same row, `attempt + 1`, proceed | the 03:00 reboot (or an OOM kill, or a deploy) killed it mid-way |
| `running`, owner alive | `RunInProgress` - the caller backs off | two workers (should not happen; see below) |

**Who owns a run - (D-W4).** The worker holds a Postgres **session advisory lock**
(`pg_try_advisory_lock(<constant for bss billing>)`) for as long as it runs anything. A
lock dies with its connection, so after a reboot or a kill the lock is free the moment
the old process is gone - no timeout to tune, no clock skew, no false takeover of a slow
but alive run. "Owner alive" = the lock is held by another session. A
`heartbeat_at` column is still written (every account, at most every few seconds) - not
to decide takeovers, but so the UI and the freshness signal can show "running, last
progress 14:02:31".

**Takeover is safe because `bill_account` is idempotent**: an account the killed run had
already billed for D answers with an empty result (proved in stage 3, example 11); an
account it was in the middle of was rolled back (one transaction per account). Nothing
is skipped, nothing is written twice; the EXCLUDE constraints are the backstop.

Run-level bookkeeping added to `billing_run` (migration 0004): `trigger`
(`scheduled | catchUp | retry`), `attempt`, `heartbeat_at`, `accounts_total`,
`accounts_done`, `accounts_failed`. Per-account outcomes in a new table
`billing_run_account (run_id, account_id, status ok|failed, error, finished_at)`, primary
key `(run_id, account_id)`.

## 3. Replay of missed dates, in order

On every wake-up (the schedule, a boot, a manual request) the worker computes

    due = [ (last completed run date + 1) ... today ]   (today = clock.today())

- empty -> nothing to do;
- otherwise it runs each date **in order, each with its own `billing_run`**, exactly as
  decided in stage 3: run D sees order-driven facts completed before 00:00 of D, so a
  replay after an outage writes the same rows and bills as the daily runs would have
  (proved in stage 3 by `test_replay_after_an_outage_...`).
- A date is never run before 00:00 of that date (it is not "due" before then), so the
  end-of-day rule holds whatever time the worker wakes.
- First start ever (no run at all): `due = [today]` - there is nothing to replay before
  the worker existed. Products started earlier are caught up by the core's catch-up on
  that first date (charges for every missed period, with today's run date).

**If a date fails, do later dates still run? - (D-W1, D-W3).** Two kinds of failure:

- **Account-level** (`BillingError` for one account: a missing price, an inconsistent
  state): recorded in `billing_run_account`, the run **continues with the other
  accounts**, and the worker **moves on to the next date**. The failing account is
  caught up automatically by the next successful run after the cause is fixed (the
  core's catch-up), with that later run date - correct net, later bill.
- **Run-level** (anything else: the database gone, a bug outside `bill_account`): the run
  is marked `failed` with the error and the worker **does not advance** past D; it
  retries D with backoff (5 min, 15 min, then hourly) until it succeeds or the owner
  intervenes. Advancing past a date that never ran would bill every account later than
  necessary for no benefit.

**Run status after a partial failure - (D-W2).** `failed` as soon as one account failed
(`accounts_failed > 0`), with `error` = "N of M accounts failed". It matches the
design's red banner ("Some accounts were not processed; charges are incomplete") and
keeps the enum (`running | completed | failed`) unchanged. Alternative: a fourth status
`partial`.

## 4. Status flips (cosmetic for money, visible in TMF637 and the UI)

Billing is driven by dates; statuses only show what the dates already say. At the start
of run D, before billing, in one transaction:

- `created | pendingActive -> active` where `start_date <= D`;
- `pendingTerminate -> terminated` where `termination_date <= D`;

**only for products whose order is visible to run D** - the same rule as billing
(creating/terminating order completed before 00:00 of D). So a product ordered today to
start today becomes `active` in tomorrow's run, which is also when it is first charged,
and a replay sets the same statuses as the daily runs did. The flips are idempotent
`UPDATE ... WHERE status = ...` statements and write no order (orders create and end
products; a flip only records that a date has passed). `cancelled` products are never
touched.

## 5. Schedule

- **00:30 BILLING_TZ** (`BILLING_RUN_AT`, decided D7 in stage 5): after 00:00 (the
  end-of-day rule), well before the 02:15 backup and the 03:00 reboot.
- The worker wakes at least every 60 s and re-reads the clock, rather than sleeping
  until 00:30 in one go - robust against suspended VMs and clock jumps, and it also
  serves manual requests within a minute.
- On boot (`restart: unless-stopped`, so after every 03:00 reboot) it computes `due` at
  once: a run killed by the reboot is taken over at ~03:01, not the next night.
- A long catch-up may overlap the 02:15 backup: harmless (`pg_dump` takes a consistent
  snapshot), only slower on 1 vCPU.
- Startup check: `BILLING_RUN_AT` must be after 00:00 and before 02:00 - **(D-W13)** - so a
  later edit cannot silently put the run into the backup or the reboot.

## 6. Internal API for the Billing runs screen (deferred UI)

Same conventions as `/internal/v1` (strict OAS in `spec/internal/`, contract tests, TMF
Error bodies). "Run manually for any date" from the design becomes **"catch up to date
X"**: runs exist per date and are replayed in order, and an `as_of` earlier than existing
data raises in the core - so the only meaningful manual action is "do now what is due up
to X" (X <= business date).

| Endpoint | Behaviour |
|---|---|
| `GET /billingRun?limit=14` | newest first: `{id, runDate, status, trigger, attempt, startedAt, finishedAt, heartbeatAt, accounts {total, done, failed}, charges {count, amounts: Money[] (one per currency)}, error, isLatest}` |
| `GET /billingRun/{id}` | the same, one run |
| `GET /billingRun/{id}/accounts?status=failed` | per-account failures with the error, for the red error box |
| `POST /billingRun/catchUp {upTo}` | `upTo` <= business date (422 otherwise); 409 while a run is running; queues a request the worker picks up within a minute; **202** with the dates that will run (`[]` and 200 if nothing is due) |
| `POST /billingRun/{id}/retry` | only the **latest** run and only if `failed` (409 otherwise: an older date's charges were already caught up by later runs); re-runs that date - the accounts that succeeded are no-ops, the failed ones are tried again; 202 |

**The API never runs billing in the request (D-W6).** It inserts a row into a small
`worker_request` table (`kind catchUp|retry`, `up_to`, `run_id`, `requested_at`,
`requested_by` = the operator from `X-Remote-User`, `picked_at`, `done_at`); the worker
polls it every wake-up (<= 60 s). Long work stays out of the API process (384 MB,
0.5 CPU, request timeouts) and there is exactly one place that runs billing.

The UI (later) polls `GET /billingRun?limit=14` every few seconds while a run is
`running` (progress = `accounts.done / accounts.total`).

## 7. Freshness signal for the external monitor

**`GET /billing-status`**, modelled on vps-infra's `/backup-status` - **(D-W7)**:

- cheap (one indexed query, like `/healthz`), unauthenticated through a Caddy carve-out
  like `/healthz` (a vps-infra change), and it returns nothing beyond status;
- **expected date** = today if the local time is past `BILLING_RUN_AT` + 2 h grace, else
  yesterday;
- **200** `{"status": "ok", "lastCompletedRunDate": "...", "expected": "..."}` when the
  run for the expected date is `completed`;
- **503** `{"status": "stale"|"failed"|"running-too-long", ...}` otherwise - an external
  uptime checker alerts on the status code alone.
- Deliberately **not** part of `/healthz`: a late billing run must not mark the app
  unhealthy (the deploy gate and Docker's healthcheck depend on `/healthz`).

## 8. Compose service and deployment

`compose.prod.yaml` gets a second service from the same image (**needs approval when
implemented: compose files and scripts are protected**):

    worker:
      image: same as app (IMAGE_NAME:TAG)
      command: ["python", "-m", "app.worker"]
      restart: unless-stopped          # back after every 03:00 reboot, takes over
      mem_limit: 256m, memswap_limit: 256m, cpus: 0.25     # (D-W8)
      read_only: true, tmpfs: [/tmp], no-new-privileges
      environment: APP_ENV, LOG_LEVEL, DATABASE_URL, BILLING_TZ, BILLING_RUN_AT
      networks: [shared]               # reaches `postgres`; no alias, no ports
      logging: json-file, 10m x 3
      healthcheck: a liveness file in /tmp touched every wake-up; unhealthy if older
                   than 3 minutes (the worker serves no HTTP)

- **Resources**: billing one account is a few queries; the whole run for a demo-sized
  base takes seconds. 256 MB / 0.25 CPU keep the VPN's share untouched; the app keeps
  its 384 MB / 0.5.
- **Deploy** (`scripts/deploy.sh`, protected): migrations (0004) first, then `up -d` of
  both services. A deploy that restarts the worker mid-run is covered by the takeover;
  optionally deploy.sh waits up to N minutes for an idle worker first **(D-W11)**.
- **Smoke** after deploy: `GET /billing-status` answers (200 or a 503 with a known
  status), and the worker container reports healthy.
- **Local**: a `worker` service in `compose.override.yaml` behind `profiles: [worker]`
  (not started by `make up`), plus `make worker-once` (one wake-up now, then exit) for
  manual testing against the seeded stack **(D-W14)**.

## 9. Migration 0004 (sketch)

- `billing_run`: `trigger` (enum, ours), `attempt int NOT NULL DEFAULT 1`,
  `heartbeat_at timestamptz`, `accounts_total|done|failed int NOT NULL DEFAULT 0`.
  Defaults are fine here: existing rows (local seeds; none in production, which has no
  worker yet) are complete runs.
- `billing_run_account` and `worker_request` as above.
- No change to ABRs, bills or products.

## 10. Tests and negative proofs (plan)

- pure: `due(clock, last completed)`, the expected date of the freshness signal, the
  wake-up decision - on FixedClock values around 00:00, 00:30, 02:30, DST-free and a DST
  zone;
- `start_run`: every row of the table in section 2; takeover proven with two
  connections - one holds the advisory lock (alive: `RunInProgress`), then closes
  (gone: takeover, no duplicate ABR);
- isolation: one account with no price -> run `failed`, `accounts_failed = 1`, every
  other account billed; after adding the price, the next run catches it up;
- run-level failure: the database connection dropped mid-run -> `failed`, no advance,
  retried;
- replay: the stage 3 outage test driven through the worker;
- flips: statuses after run D for orders completed before and after 00:00 of D;
- API and freshness endpoint: contract tests, each status code;
- negative proofs: takeover without the lock check (two workers -> duplicate attempts),
  advancing past a run-level failure, flipping by status instead of dates, a freshness
  check that ignores `failed`.

## Decisions needed from the owner

| # | Question | Recommendation |
|---|---|---|
| D-W1 | After an account fails, continue with other accounts and later dates? | **Yes**: isolate per account; the core catches the account up once fixed |
| D-W2 | Run status when some accounts failed | **`failed`** (matches the design banner; enum unchanged), not a new `partial` |
| D-W3 | Run-level failure (DB down, bug) | **Do not advance**; retry the same date with backoff 5 / 15 / 60 min |
| D-W4 | How to detect a dead run | **Postgres advisory lock** held by the worker; heartbeat only for display |
| D-W5 | Status flips | **In the worker, at run D, before billing, with billing's visibility rule** |
| D-W6 | How the UI triggers work | **`worker_request` table polled by the worker**; the API never bills in-request |
| D-W7 | Freshness signal | **`GET /billing-status`**, unauthenticated (Caddy carve-out like /healthz), 200 / 503, 2 h grace after 00:30 |
| D-W8 | Worker limits | **256 MB, 0.25 CPU**, read-only, no ports |
| D-W9 | Retry scope | **Latest failed run only**; re-runs the date, succeeded accounts are no-ops |
| D-W10 | Manual "run any date" | **Replaced by "catch up to date X"** (X <= business date), already agreed in stage 5 |
| D-W11 | Deploy during a run | Rely on takeover; optionally deploy.sh waits for idle (protected script) |
| D-W12 | Alerting beyond the HTTP monitor (mail, chat) | Out of scope for now |
| D-W13 | Guard on `BILLING_RUN_AT` | Refuse to start unless 00:00 < t < 02:00 |
| D-W14 | Local worker | Compose profile `worker` + `make worker-once` (protected files) |
| D-W15 | First start with no runs | `due = [today]` only; earlier periods are caught up by the core on that date |
