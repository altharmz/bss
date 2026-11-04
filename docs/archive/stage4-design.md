# Stage 4 - the billing worker: design

Built (migration 0004, `app/worker/`). Every rule from stage 3 stays as it is
([decisions/billing-core.md](../decisions/billing-core.md), "Billing (stage 3)"); this
note decides who calls `bill_account`, when, and what happens when it does not go to
plan. Section numbers and the D-W/Q labels are cited by code; the decisions as built are in
[decisions/worker.md](../decisions/worker.md).

## 1. What the worker is

A second process from the **same image** as the app: `python -m app.worker`. No HTTP
server, no ports. It owns everything time-driven:

1. the daily billing run at **00:30 BILLING_TZ** (`BILLING_RUN_AT`, already in settings
   and shown by `/internal/v1/context`);
2. replay of missed dates, in order;
3. the product status flips (`created -> active`, `pendingTerminate -> terminated`);
4. requests from the UI, taken from a queue table (catch up to a date, retry a run).

The worker reads time only through the injected clock (`app/clock.py`); in tests it runs
on a `FixedClock`, and every decision below is a pure function of (clock, database
state) so it can be tested without sleeping. **There is never billing inside an API
request.**

## 2. Run lifecycle

The `billing_run` row per date exists since stage 1 (`run_date` UNIQUE, status
`running | completed | failed`, `started_at`, `finished_at`, `error`). Stage 4 makes
`start_run` answer every case instead of raising `BillingRunConflict` for two of them
(the open question then in CLAUDE.md, resolved since: [decisions/worker.md](../decisions/worker.md#resolved-questions)):

| Row for D | `start_run(D)` | Why |
|---|---|---|
| none | insert `running`, proceed | normal |
| `completed` | **`AlreadyCompleted` - a no-op**, not an error | replays, a second worker start, a catch-up overlapping the schedule |
| `failed` | back to `running`, `attempt + 1`, proceed with **only its failed accounts** | retry |
| `running`, **its owner is gone** | **take over**: same row, `attempt + 1`, proceed | the 03:00 reboot (or an OOM kill, or a deploy) killed it mid-way |
| `running`, owner alive | `RunInProgress` - the caller backs off | two workers (should not happen) |

**Owner liveness = a Postgres session advisory lock** (decided). The worker holds
`pg_try_advisory_lock(<constant for bss billing>)` while it runs anything. The lock dies
with its connection, so after a reboot or a kill it is free the moment the old process
is gone - no timeout to tune, no false takeover of a slow but alive run. A
`heartbeat_at` column is still written (per account, at most every few seconds), only so
the UI can show "running, last progress 14:02:31".

**Takeover is safe because `bill_account` is idempotent**: an account the killed run
had already billed for D answers with an empty result (stage 3, example 11); an account
it was in the middle of was rolled back (one transaction per account). The EXCLUDE
constraints are the backstop.

## 3. Progress per (run, account) - failure isolation

Isolation is **per account** (decided). Every account's outcome for every run is a row:

    billing_run_account (run_id, account_id, status, error, attempts, finished_at)
      status: ok | failed | blocked        PRIMARY KEY (run_id, account_id)

- **`ok`** - `bill_account` succeeded for this date.
- **`failed`** - `bill_account` raised a `BillingError` (a missing price, an inconsistent
  state); the error text is stored. Nothing was written for the account (its
  transaction rolled back).
- **`blocked`** - the account has an unresolved failure on an **earlier** date, so it is
  **not billed for this date** (decided: a failed account is not billed for later dates
  until fixed). The row says which date it waits for.

**Why blocked accounts are not billed later**: billing an account at D+1 while D failed
would let the core's catch-up charge D's periods with D+1's run date (correct net, but
different rows and bills than if D had worked) and would make D impossible to retry
later (an `as_of` earlier than the account's existing data raises). Keeping the account
at D means that, once fixed, it is billed for D, D+1, ... **in order, each with its own
run date**, so it ends with exactly the rows and bills it would have had without the
failure - the same replay equivalence as stage 3's outage test, per account. (Run D
sees order-driven facts completed before 00:00 of D, whenever it is processed.)

**After a fix** (a retry - by the operator or automatically, see D-W17): the worker
re-runs the account's earliest failed date; if that succeeds it continues with the
account's `blocked` dates in order, updating each `(run, account)` row to `ok`. If it
fails again, the account stays where it is. Other accounts are never held up: they
proceed normally on every date (decided).

**Run status** (decided: any failed account -> run `failed`, with per-account details):

- `completed` - every account of the run is `ok`;
- `failed` - at least one account is `failed` (and, proposed, also when one is
  `blocked` - see D-W16); `error` = "N of M accounts failed", the details in
  `billing_run_account`;
- after a successful retry/catch-up the run's status is recomputed - a run whose
  accounts all became `ok` turns `completed`.

**Retry** (decided): retrying a failed run reprocesses **only its failed accounts**; the
accounts that were `ok` are not touched (they would be no-ops anyway), and its `blocked`
accounts follow automatically once their earlier date succeeds. Any failed run may be
retried; retrying a later run does nothing for accounts blocked behind an earlier date.

**Whole-run failure** (decided: does not advance): anything that is not an account's
`BillingError` - the database gone, a bug outside `bill_account`, the lock lost - marks
the run `failed` with the error, and the worker **does not move past that date**; it
retries the same date with backoff (5 min, 15 min, then hourly) until it succeeds.

Run-level bookkeeping added to `billing_run` (migration 0004): `trigger`
(`scheduled | catchUp | retry`), `attempt`, `heartbeat_at`, `accounts_total`,
`accounts_ok`, `accounts_failed`, `accounts_blocked`.

## 4. Replay of missed dates, in order

On every wake-up the worker computes

    due = [ (latest run date that exists) + 1 .. today ]

and runs each date in order with its own `billing_run`; within a date, every account
that is not blocked is billed. (A blocked account's own dates are caught up separately,
in order, after its fix - section 3.) A date is never due before 00:00 of that date (the end-of-day rule).
First start ever (no run at all): `due = [today]` (D-W15); periods before are caught up
by the core on that date.

## 5. Status flips (cosmetic for money, visible in TMF637 and the UI)

At the start of run D, before billing, in one transaction:

- `created | pendingActive -> active` where `start_date <= D`;
- `pendingTerminate -> terminated` where `termination_date <= D`;

**only for products whose order is visible to run D** (completed before 00:00 of D) -
billing's own rule, so a replay sets the same statuses the daily runs did. Idempotent
`UPDATE ... WHERE status = ...`; no order is written (orders create and end products; a
flip only records that a date passed); `cancelled` is never touched. A blocked account's
products are still flipped (statuses follow dates, not billing) - see D-W5.

## 6. Schedule

- **00:30 BILLING_TZ** (`BILLING_RUN_AT`): after 00:00 (end-of-day rule), well before
  the 02:15 backup and the 03:00 reboot.
- The worker wakes at least every 60 s and re-reads the clock (robust against suspended
  VMs and clock jumps; serves the queue within a minute).
- On boot (`restart: unless-stopped`, so after every 03:00 reboot) it acts at once: a run
  killed by the reboot is taken over at ~03:01.
- A long catch-up may overlap the 02:15 backup: harmless (`pg_dump` takes a consistent
  snapshot), only slower on 1 vCPU.

## 7. Internal API for the Billing runs screen (deferred UI)

Same conventions as `/internal/v1` (strict OAS in `spec/internal/`, contract tests). The
design's "Run manually for any date" becomes **"catch up to date X"** (X <= business
date). **The API never bills**: it writes a `worker_request` row (`kind catchUp|retry`,
`up_to`, `run_id`, `requested_at`, `requested_by` = the operator from `X-Remote-User`,
`picked_at`, `done_at`) and the worker takes it within a minute (decided).

| Endpoint | Behaviour |
|---|---|
| `GET /billingRun?limit=14` | newest first: `{id, runDate, status, trigger, attempt, startedAt, finishedAt, heartbeatAt, accounts {total, ok, failed, blocked}, charges {count, amounts: Money[] (per currency)}, error, isLatest}` |
| `GET /billingRun/{id}` | one run |
| `GET /billingRun/{id}/accounts?status=failed|blocked` | per-account outcomes with the error / the date it waits for |
| `POST /billingRun/catchUp {upTo}` | `upTo` <= business date (422 otherwise); queues the request; **202** with the dates that will run, or 200 `[]` if nothing is due |
| `POST /billingRun/{id}/retry` | the run must be `failed` (409 otherwise); queues a retry of its failed accounts; 202 |

The UI polls `GET /billingRun?limit=14` every few seconds while a run is `running` or a
request is pending.

## 8. Freshness signal for the external monitor

**`GET /billing-status`** (unauthenticated, exposing only the date, the status
and the age - no amounts, no names, no counts):

    200 {"date": "2026-10-01", "status": "ok", "ageSeconds": 3600}
    503 {"date": "2026-09-30", "status": "stale" | "failed" | "running", "ageSeconds": 97200}

- `date` - the latest run date; `status` - `ok` if that run is `completed` and it is the
  expected date, otherwise why not; `ageSeconds` - since that run finished (or started,
  if it has not).
- **expected date** = today once the local time is past `BILLING_RUN_AT` + a grace
  period, else yesterday (D-W19).
- One indexed query, like `/healthz`; served without auth through a Caddy carve-out like
  `/healthz` (a vps-infra change). Deliberately **not** part of `/healthz`: a late run
  must not mark the app unhealthy (the deploy gate and Docker's healthcheck use it).

## 9. Compose service and deployment

`compose.prod.yaml` gets a second service from the same image (**protected file - needs
approval when implemented**):

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

- **Resources**: billing one account is a few queries; a demo-sized run takes seconds.
  256 MB / 0.25 CPU keep the VPN's share untouched; the app keeps its 384 MB / 0.5.
- **Deploy** (`scripts/deploy.sh`, protected): migrations (0004) first, then `up -d` of
  both services. **A deploy never interrupts an account mid-transaction** (decided):
  - the worker handles SIGTERM (what `docker compose up`/`stop` send) by finishing the
    account it is billing - its transaction commits or rolls back as a whole - then
    releasing the advisory lock and exiting; it takes no new account after the signal;
  - the service gets `stop_grace_period: 60s`, far more than one account takes, so
    Docker's SIGKILL never lands inside an account;
  - the rest of the run is taken over by the new worker (section 2), and accounts already
    `ok` are not touched again.
  Even a SIGKILL (OOM, power) cannot leave half an account written - one transaction per
  account - but the graceful stop makes the normal deploy path clean as well. Waiting
  for an idle worker in deploy.sh is not needed.
- **Smoke**: `GET /billing-status` answers with a known status, and the worker container
  is healthy.
- **Local** (decided: one-shot via a make target): `make worker-once` runs one wake-up of
  the worker against the local stack - due dates, the queue, the flips - and exits; no
  long-running local worker. It is a `docker compose run --rm app python -m app.worker
  --once` (Makefile, protected).

## 10. Migration 0004

`billing_run` gains `trigger` (`scheduled | catchUp | retry`), `attempt`, `heartbeat_at`
and the four `accounts_*` counters; `billing_run_account` (section 3) and `worker_request`
(section 7) are new. No change to ABRs, bills or products.

## Decided: isolation, retry, ownership

- **Isolation per account**, tracked per (run, account) with status and error. A failed
  account is **not billed for later dates** until fixed; then it **catches up its dates
  in order**. **Retrying a failed run reprocesses only its failed accounts.** Other
  accounts proceed normally. (Was D-W1, D-W9.)
- **Any failed account -> the run is `failed`**, with per-account details. (Was D-W2.)
- **A whole-run failure (DB down) does not advance past that date**; retried with
  backoff. (Was D-W3.)
- **The advisory lock decides owner liveness.** (Was D-W4.)
- **The UI goes through a queue table; never billing inside an API request.** (Was
  D-W6.)
- **`/billing-status` is unauthenticated and exposes only the date, the status and the
  age** - no amounts or names. (Was D-W7.)
- "Run any date" is replaced by **"catch up to date X"** (agreed in stage 5). (Was D-W10.)

## Decided: gaps, deploys, alerting

- **D-W18 - gaps are prevented at the catalog** (implemented, commit `fd0c0a7`): ending
  or moving the end of a recurring price is a 409 if any product of that offering, in
  that currency, is not terminated by the first day the price no longer covers, unless a
  successor price starts exactly at the new end. **No operator waiver**: after this rule
  a missing price is a **data bug**, handled case by case with a reviewed one-off script
  and an audit note, not a UI feature. **Never backdated prices** (unchanged). So a
  `MissingPrice` failure in a run means the data is wrong; the account stays blocked
  until the script has fixed it, then catches up in order.
- **D-W16 - a `blocked` account makes the run `failed`** (its charges for that date are
  missing).
- **D-W17 - the worker retries failed accounts itself, once per scheduled run** (00:30),
  before the new date; the operator's retry is for immediacy.
- **D-W5** - status flips also for blocked accounts (statuses follow dates).
- **D-W8** - worker limits 256 MB, 0.25 CPU, read-only, no ports.
- **D-W11** - **a deploy never interrupts an account mid-transaction**: graceful SIGTERM
  (finish the current account, release the lock, exit) and `stop_grace_period: 60s`;
  the rest of the run is taken over (section 9). No wait-for-idle in deploy.sh.
- **D-W12** - **alerting goes through the existing external monitor via
  `/billing-status`**; no mail/chat integration.
- **D-W13** - the worker refuses to start unless 00:00 < `BILLING_RUN_AT` < 02:00.
- **D-W14** - **the local worker runs one-shot via a make target** (`make worker-once`),
  no long-running local service.
- **D-W15** - first start with no runs: `due = [today]`; earlier periods are caught up by
  the core on that date.
- **D-W19** - freshness grace 2 h after `BILLING_RUN_AT`; a run `running` longer than
  that reports 503 `running`.

## Decided: price gaps

- **D-W20 - ordering rejects a price gap** (`60f2a58`): an add item is rejected with
  `priceEnds` unless, from the product's start date, the offering's recurring prices in
  the account currency form a gapless chain ending in an open-ended price; the whole
  chain is walked. Together with D-W18 this is the invariant **"a live product has a
  price for every future day"** - so a `MissingPrice` in a run can only come from data
  changed outside the API (a data bug: reviewed one-off script + audit note).
- **D-W21 - the atomic price change** (`1a02efa`, UI `3562fd4`):
  `POST /internal/v1/productOffering/{id}/priceChange {amount, currency, validFrom}`
  ends the open-ended price at midnight (BILLING_TZ) of `validFrom` (>= tomorrow) and
  creates the attached successor, in one transaction; documented in [decisions/catalog.md](../decisions/catalog.md) as our
  deviation from TMF620 (no cross-request transaction, and the no-overlap constraint).
  The catalog dialog has a "Change price" action whose copy states that existing
  products pick up the new price from their next billing period starting on or after the
  date, new orders from the date.
