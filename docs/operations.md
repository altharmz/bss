# Operations

How to develop, test, deploy and watch bss. This is a summary with links; the binding
rules are in [CLAUDE.md](../CLAUDE.md) and [decisions/](decisions/README.md) (sections
named in brackets below), which win where the two differ.

## Local development (WSL2)

Everything runs in containers: `docker compose` merges `compose.yaml` with
`compose.override.yaml` (local PostgreSQL `db`, the app with hot reload on
`127.0.0.1:8081` (`APP_PORT`), read-only source mounts; Node services for the UI). **Use the make
targets; do not invent commands** (CLAUDE.md, "Commands").

| Target | What |
|---|---|
| `make up` / `make down` / `make logs` / `make ps` | start (and wait for healthy), stop, last 5 min of app logs, status |
| `make migrate` | `alembic upgrade head` against the local stack |
| `make seed` | demo data (US names, USD amounts, numbered mobile, categories and catalogs) **through the public TMF API** with a FixedClock that walks 120 days forward, billing each day; refuses `APP_ENV=prod` and any database but the local one; a second run is a no-op (`tests/seed.py`, `tests/test_seed.py`) |
| `make worker-once` | one wake-up of the billing worker, then exit (there is no long-running local worker) |
| `make psql` | interactive psql in the local db (the db port is not published) |
| `make test` | `pytest -n auto` in the app container |
| `make lint` / `make fmt` | ruff check + format check / auto-format (the writable `tools` service) |
| `make smoke` | `scripts/smoke.sh` against the published app port: `/healthz`, `/`, `/billing-status` |
| `make progress` | write `.progress/PROGRESS.md` and `.progress/status.json` locally (not committed) from [progress/scope.yaml](progress/scope.yaml) and the code |
| `make docs` / `make docs-check` | regenerate [api/](api/README.md) and [db/](db/README.md) / fail if the committed ones differ |
| `make clean` | stop the stack **and drop the local database volume** |
| `make web-install` | the only step that downloads npm packages (`npm ci`, exactly the lockfile) |
| `make deploy-web-install` | the same `npm ci`, **primary checkout only** (`web-install` is refused there): run it when `make deploy` fails its UI build with "run make deploy-web-install" after `package-lock.json` changed |
| `make web-dev` / `web-build` / `web-lint` / `web-test` / `web-types` / `web-fmt` / `web-e2e` | the UI, always in the `web` / `web-offline` containers; offline targets fail with "run make web-install" if node_modules does not match the lockfile |
| `make hooks` | install the shared pre-commit and pre-push hook dispatchers |
| `make spec-verify-upstream` | re-fetch the spec blob SHAs from GitHub and compare with `spec/README.md` (network) |

The local PostgreSQL runs with `fsync`, `full_page_writes` and `synchronous_commit` off
for test speed - not crash-safe; recover with `make clean && make up` ([decisions/infra.md](decisions/infra.md#conventions),
"Conventions"). Node never runs on the host ([decisions/frontend.md](decisions/frontend.md#frontend-conventions)).

## Tests

| Suite | Command | What it covers |
|---|---|---|
| Backend | `make test` (`pytest -n auto`) | every test gets its own database cloned from a template migrated to head and dropped afterwards (`tests/conftest.py`); every API response is validated against `spec/` (`tests/apiclient.py`); hypothesis properties with 50 examples (`dev`) or 200 in `make check` (`ci`, `HYPOTHESIS_PROFILE`) |
| Migrations | part of `make test` | upgrade on an empty database, downgrade to base and up again, as a NOSUPERUSER database owner like production, data-carrying upgrades of 0002 and 0004, and 0005's comments (`tests/test_migrations.py`, `tests/test_nonsuperuser_migration.py`) |
| Worker processes | part of `make test` | real `python -m app.worker` processes on a pinned clock, SIGTERM and SIGKILL (`tests/test_worker_signals.py`) |
| UI unit/component | `make web-test` | vitest, twice (UTC and `America/New_York`) |
| UI lint | `make web-lint` | tsc, eslint, prettier |
| UI end-to-end | `make web-e2e` | Playwright on the built UI against the seeded local stack, read-only |
| Generated docs | `make docs-check` | docs/api and docs/db match the code |

## Hooks

- **Pre-commit** (`scripts/pre-commit.sh`, installed by `make hooks`): refuses the commit
  unless the working tree is exactly what is committed, then runs lint, the smoke tests and
  the tests the change affects (the whole backend suite for specs, pins, migrations or the
  worker), plus UI and generated-docs checks when their sources change.
  It is the one local test run of a development cycle; the full `make test` is nightly in CI
  and by hand before a PR that touches billing, migrations or the API. Never skipped.
- **Pre-push** (`scripts/pre-push.sh`): scans only the commits being pushed and refuses a
  bcrypt hash, an added `.env*` path (other than `.env.example`), a branch deletion or a
  non-fast-forward. Details: [decisions/harness.md](decisions/harness.md#development-cycle-and-gates).

## Deploy

`make deploy` runs `scripts/deploy.sh`, the **only** script allowed to change anything on
the VPS; `make verify` is the same. The commit was already tested by the pre-commit
hook, so deploying does not test again; it exits non-zero and prints remote logs when
anything fails. `make check` (`lint`, `test` with hypothesis `ci`, `docs-check`,
`web-lint`, `web-test`, `web-e2e`) runs on demand - for a change that needs stronger
testing.

VPS releases disable account PIN verification for protected prepaid actions by default.
To require verification, set `BSS_ACCOUNT_PIN_REQUIRED=true` in the workstation's
`.env.prod`. Production Compose passes the setting to the app on each release.

What `deploy.sh` does, in order:

1. **Refuses a dirty tree** (`git status --porcelain`): the image tag is the commit hash.
2. **Refuses without the smoke secret** (`BSS_SMOKE_SECRET`) in the local `.env.smoke`,
   and without the service and smoke secrets in `.env.prod`. Builds the UI locally with the
   commit as its `bss-build` marker. `DRY_RUN=1` stops here and lists exactly what would
   be shipped.
3. Opens **one SSH master connection** for the whole deploy (timeouts, keepalives,
   `BatchMode`; one retry only when opening it fails).
4. Checks that vps-infra's `shared` network exists ("deploy vps-infra first").
5. **Builds the runtime image locally** - never on the VPS - and ships it with `docker
   save | gzip | ssh docker load`, skipped when the server already has an image with the
   same fingerprint (layers + config).
6. On the first deploy after releases were introduced (no `~/bss/CURRENT` yet) it records
   the running release as one (**bootstrap**: tag from the running app container, compose
   files and `web/` from `~/bss`, commit from `DEPLOYED`, `alembic current`) and makes it
   `CURRENT`. With nothing running it says no rollback is possible for this deploy.
7. Syncs `.env.prod` (as `~/bss/.env`), and stores the release in
   `~/bss/releases/<tag>/`: compose files, the exact UI files (`web/`) and `release.env`
   (`tag`, `commit`, `alembic_rev` = the newest migration of the commit, `deployed_at`).
   The compose files in `~/bss` itself are not touched yet.
8. **Runs the migrations** with the new image before the app serves traffic (with a
   deadline), from the compose files in `releases/<tag>/`. A failure up to here changes
   nothing that serves: the old containers keep running, the schema is unchanged (all
   pending migrations run in one transaction, `alembic/env.py`) and
   `~/bss/compose*.yaml` are still the running release's; the message says "nothing
   changed on the server; `<tag>` is still serving". (`~/bss/.env` is already the new
   one: it is not kept per release.)
9. `up` of the new release (its compose files copied from `releases/<tag>/` to `~/bss`) - **the point of no
   return**: any failure from here on rolls back automatically ([Rollback](#rollback)).
   Waits until **`app`, `worker` and `mcp` are healthy** (240 s).
10. Atomically writes the full Git commit to `~/bss/DEPLOYED`, reads it back and
   requires an exact match. The smoke output prints the commit and image tag. The same
   commit goes to the workstation's `.deployed` (gitignored) with tag, subject, last
   migration, UTC time and `checks=running`; after the last step `checks=passed`.
   `make deployed` prints it and the commits since.
   Smoke on the VPS: `/healthz` and `/billing-status` (200 must say `ok` or
   `running`; 503 one of `stale|failed|running|none`; `scripts/billing-status-check.sh`,
   shared with `make smoke`).
11. Syncs the UI **into** `~/bss/web` from `releases/<tag>/web/`, on the server (hashed
    assets, then `index.html`, then a `--delete` pass; the directory is never replaced,
    so Caddy's bind mount keeps it, and it always equals a stored release).
12. **Web smoke** through Caddy (`scripts/web-smoke.sh`; Caddy asks for no login, the app
    is the boundary): this build's `index.html`, a deep link and a hashed asset without
    credentials, `/internal/v1/context` 401 JSON from the app without them, with the
    read-only smoke secret 200 JSON with `operator` = `smoke`, a TMF list with
    `X-Result-Count` and no `X-Total-Count` (#312), `POST` -> 403 from the app, `/healthz` open.
13. Runs the MCP discovery, authentication and OAuth smoke checks through Caddy, then the
    **OAuth login smoke** (`scripts/mcp-oauth-smoke.py`) as the `mcp` smoke user from
    `.env.smoke` (`MCP_SMOKE_USER`, `MCP_SMOKE_PASSWORD`): read scope only, one read tool,
    a write tool refused, refresh, revoke. Without those lines it prints SKIPPED.
    The web, MCP and OAuth smokes retry a request that got no answer (3 attempts, 3 s
    apart, each retry logged) when that is safe - a GET, or a request that never reached
    the server; a wrong answer fails at once ([decisions/infra.md](decisions/infra.md#smoke-retries-2026-11-08)).
14. Every gate passed: the pointers move - `PREVIOUS` = the old `CURRENT`, `CURRENT` = this
    tag (a re-deploy of `CURRENT` leaves `PREVIOUS` alone). Then it prunes the smoke's MCP
    client registrations (a warning on failure), old `bss-app` images (the 3 newest, and
    never `CURRENT` or `PREVIOUS`; nothing else is touched) and every `releases/` directory
    but `CURRENT`'s and `PREVIOUS`'s. Pruning failures are warnings and never roll back.

A deploy is **reversible unless a schema downgrade refuses** because it would lose data
written since the deploy ([Rollback](#rollback)). `DEPLOYED` records the backend that
runs; `.deployed` with `checks=running` after a deploy means the script was interrupted
after the health gate.
Production config, limits and the constraints of the VPS (VPN ports, no prune, nothing
published on 0.0.0.0): see [decisions/infra.md](decisions/infra.md). Never run `docker build`
on the VPS.

## Rollback

`scripts/deploy.sh` keeps the last two releases on the VPS: `~/bss/releases/<tag>/` for
`CURRENT` and `PREVIOUS` (`~/bss/CURRENT`, `~/bss/PREVIOUS`, one tag each) and their
`bss-app` images. Secrets are not part of a release: there is one `~/bss/.env`. Decisions:
[decisions/infra.md](decisions/infra.md#automated-rollback-2026-11-08).

**Automatic.** A failure after `up` of a new release (health gate, `DEPLOYED`, `/healthz`,
`/billing-status`, UI sync, web, MCP or MCP OAuth smoke) rolls back to the release that
served before, then `make deploy` still exits 1 with "DEPLOY FAILED, ROLLED BACK to `<tag>`
(`<commit>`); the failure was: ...". **Manual:** `make rollback` goes from `CURRENT` to
`PREVIOUS` and exits 0. Both run the same steps:

1. Refuse with "cannot roll back: no previous release on the server - deploy instead" when
   there is no previous tag, or its `releases/` entry or image is missing.
2. Stop `app worker mcp` of the release being left.
3. If the two `alembic_rev` differ: `alembic downgrade <previous rev>` **in the image of the
   release being left** (only it contains the downgrade scripts), with the migration
   deadline.
4. Copy the previous release's compose files to `~/bss`, `up` it, wait for health.
5. Sync its `web/` into `~/bss/web` on the server (same three passes as a deploy).
6. Write `DEPLOYED`, `CURRENT=<previous>`, delete `PREVIOUS`.
7. Smoke it: deployed commit, `/healthz`, `/billing-status`, web smoke with the previous
   build, MCP smoke (no OAuth smoke).
8. `.deployed`: the previous commit and tag, `checks=rolled-back`,
   `rolled_back_from=<tag>`; `make deployed` prints "rolled back from ...".

**One step only.** After a rollback there is no `PREVIOUS`: a second `make rollback` fails
with "no previous release - deploy instead". Rollback never restores a database dump.

**Refused downgrade.** A lossy downgrade refuses once the data it would drop exists (e.g.
`0030`, the prepaid `0034`-`0047`, [below](#prepaid-recovery-and-rollback)). Then nothing
is forced: the release being left is brought back up and the script stops with "ROLLBACK
REFUSED: the schema downgrade to `<rev>` failed ... `<tag>` is running again. Restoring
needs a vps-infra dump". What the human does then: fix forward (a new commit, `make
deploy`), or - only if the data written since the deploy may be lost - restore the
pre-deploy vps-infra dump (vps-infra's tooling, not this repo) and roll back by hand.

**Plan first:** `make rollback DRY_RUN=1` prints the current and previous tag, commit and
`alembic_rev`, "schema downgrade: yes|no", the compose files and the UI files that would
change, and changes nothing.

**Drill switches** (for the human-approved live drill only; printed loudly at the start,
inert unless set). `BSS_DEPLOY_DRILL=<mode> make deploy`:

| Mode | Effect |
|---|---|
| `fail-migration`, `slow-migration` | the migrations fail, or overrun the 120 s deadline, inside their transaction (`alembic/env.py`): nothing may change |
| `unhealthy` | the new release starts with `BSS_HEALTH_DRILL=unhealthy`, its `/healthz` answers 503, the health gate fails and the deploy rolls back |
| `fail-after-<point>` | fails right after that step and rolls back; points: `health`, `deployed`, `healthz`, `billing-status`, `web-sync`, `web-smoke`, `mcp-smoke`, `oauth-smoke` |
| `warn-prune` | the three cleanup steps fail; they only warn and the deploy still passes |

`BSS_ROLLBACK_DRILL=slow-downgrade make rollback` makes the schema downgrade overrun its
deadline: the rollback must refuse and leave the current release running. Runbooks:
issue #239 (no schema change) and #254 (every scenario, with the drill migration `0064`).

## Prepaid: recovery and rollback

**Rollback barrier.** Migrations `0034`-`0047` carry the prepaid schema. Each downgrade
refuses to run once the data it would lose exists: `0034` refuses any prepaid account or
bucket (so the first prepaid account created on a deployed instance ends clean
downgrade past `0033`); the later ones refuse retained holds, commands, command runs,
funded intervals and service grants, shortfall observations, manual suspensions,
balance actions, journal keys and transfer-fee charges. Do not delete history to make
a downgrade pass. A [rollback](#rollback) downgrades the schema together with the image
and stops with "ROLLBACK REFUSED" when one of these refuses; an older image over a newer
schema is never started. With prepaid history the supported recovery is forward (a fixed
image).

**Stuck or failed commands** (read-only first; the UI shows the same on the account):

1. `list_prepaid_commands` / `GET /internal/v1/prepaid/command?state=failedTechnical`
   (and `state=pending`) name the commands that did not finish. A **pending** command
   with `needs_topup` is not a fault: it completes after a credit (top-up, payment,
   released hold) on a later worker tick.
2. A **failedTechnical** command is retried by the worker with backoff; once the cause is
   fixed, `retry_prepaid_command` (or the UI's Retry) clears the backoff. Retry never
   posts: it re-queues the same identity, hold and request, so it cannot double-charge.
3. An uncertain HTTP response to a top-up, adjustment, hold, transfer or order is resent
   with the **same Idempotency-Key**; the response journal returns the first answer.
   A new key is a new action.
4. A held amount that must not wait (a lapsed operator hold, a cancelled order) is
   released by the worker; reserve holds lapse before any later spend. Read
   `balanceActionHistory` before correcting a balance, and correct with a new
   adjustment or reversal, never by editing rows.
5. After a VPS reboot nothing needs doing: the worker resumes pending commands and
   renewals from the database on its first tick.

## Backups

**Not part of this repository.** vps-infra dumps every database on the host, this one
included, by cron; bss has no backup or restore tooling ([decisions/infra.md](decisions/infra.md#shared-infrastructure-vps-infra), "Shared
infrastructure"). Make sure a fresh dump exists before any migration that alters or
removes data. Restore drill passed on 2026-11-08 (issue #9) with the cron dump
`20261108-021501` (alembic `0060`): restored locally in 4.8 s, then into the live `bss`
database in 5.1 s; `alembic upgrade head` brought it to `0063` with the same tables and enums
as production, and a fresh pre-drill dump put today's data back (about 12 min downtime). To
repeat it: stop app, worker and mcp, take a fresh dump, drop and recreate `bss`, restore,
upgrade, restore the fresh dump, start. There is **no `/backup-status` endpoint** in bss; backup
monitoring, if any, belongs to vps-infra.

**Dump size and time at 100k subscribers** (#282, 2026-11-11), measured in the local
load-test stack on the dataset of #289/#293 (`make loadtest-restore NAME=seed100k`, migrated
to `0076`, so `usage_record` partitioned): `pg_dump -Fc` of the bss database took **82.5 s**
and wrote **580.9 MB** (database 4.6 GB, of which `usage_record` 2.8 GB with 6.8M rows,
three days of usage). The same data at `0075`, unpartitioned, dumped to 580.7 MB. Workstation:
Ryzen 5 PRO 4650U (6 cores / 12 threads), WSL2 with 15 GB; `lt-db` pinned to cpuset `4-7`
(both threads of cores 2 and 3) with 3 GB; the dump piped from the container to the host.
The `usage_record` partitions alone dumped to 281.6 MB in 35.2 s (about 41.5 bytes per
record). At the target of 2M records a day that is about 83 MB of dump per day of usage,
and the 13-month window holds about 790M records, about 33 GB of dump, before retention
drops the oldest month. So the usage window, not the subscriber count, sets the nightly
dump's size and time; the rest of the database at 100k dumps to about 300 MB.

## Monitoring

| Signal | Where | Meaning |
|---|---|---|
| `GET /healthz` | app, unauthenticated | 200 when the app can reach the database, else 503 |
| `GET /billing-status` | app, unauthenticated | billing freshness: 200 `ok`/`running`, 503 `stale`/`failed`/`running`(overdue)/`none` ([billing-worker.md](billing-worker.md#billing-status)) |
| `GET /metrics` | app and mcp, bearer token ([ops/monitoring/](../ops/monitoring/README.md)) | Prometheus text format; 404 until `BSS_METRICS_TOKEN` is set. HTTP rate/latency by route template, requests in flight and those refused at the per-process bound (`bss_http_in_flight`, `bss_http_rejected_overload_total`; 503 `overloaded` beyond `BSS_MAX_IN_FLIGHT`, [infra.md](decisions/infra.md#request-bound-2026-11-19-341)), queue depth and age, billing freshness, product counts, `bss_usage_record_default_rows` (usage records of a month without its partition; alert `BssUsageDefaultPartition` when > 0) (database series cached 30 s, `bss_db_up 0` when the read fails). Worker health shows through the queue and billing series. The app counts in Prometheus multiprocess mode (`PROMETHEUS_MULTIPROC_DIR`, default `/tmp/prometheus` on the tmpfs). A scrape sums all `WEB_CONCURRENCY` processes, and gauges are `livesum`, so `bss_build_info` equals the number of processes. There are no `process_*` / `python_*` series: container CPU and memory come from `docker stats`. mcp runs one process and is unchanged |
| app healthcheck | `Dockerfile` | bash `/dev/tcp` probe of `/healthz` every 60 s |
| worker healthcheck | `compose.prod.yaml` | `/tmp/worker-alive` younger than 180 s |
| logs | `make logs` (local), json-file with rotation on the VPS | JSON lines, one per request; 500s with their traceback; see "Finding a request by its ID" |

### Finding a request by its ID

Every response carries `X-Request-Id` (a valid inbound one is echoed). Every log line of that
request has the same `request_id`, the app's unhandled-error log included; a worker wake-up
uses `wake-<8 hex>`, and an MCP tool call passes its ID on to the app request it makes.

```
make logs | jq 'select(.request_id=="<id>")'
```

Docker healthchecks are **informational only**: nothing restarts or replaces an
`unhealthy` container (`restart: unless-stopped` reacts to exits). Real alerting is the
external monitor polling `/healthz` and `/billing-status` (configured outside this
repository).

### Security scan

`.github/workflows/security.yml` scans the Python packages, the UI packages and the runtime
image every night at 01:45 UTC ([decisions/infra.md](decisions/infra.md#supply-chain-scanning-2026-11-08)).
When a run is red:

1. Read the failing job's log. The gate prints one line per blocking finding
   (`package version id severity fix-versions`) and any ignore-file problem: an expired
   review-by, a date more than 60 days ahead, or a malformed line. Trivy prints a table.
2. **Fix it:** change the pin deliberately ([decisions/infra.md](decisions/infra.md), "Dependencies are
   pinned"), or bump the base image digest for an OS package. Open an issue for the fix.
3. **Or accept it for now:** add `<id> <review-by> <reason>` to
   `ops/security/audit-ignore.txt` (pip, npm), or a `# reason` line plus `<id> exp:<date>` to
   `.trivyignore` (image). The date is at most 60 days ahead, and the reason names the issue
   that tracks the fix. An expired entry turns the run red again, which forces a new review.

Exit code 2 from the gate means the tool's JSON changed shape or the tool crashed. Fix the
gate or the tool pin; don't accept anything in that case.

## The agent harness

Every regular operation is a `make` target and agents are told to use them
([worktrees.md](worktrees.md)). The few hard guard rails exist against **agent mistakes, not a
hostile agent** ([decisions/harness.md](decisions/harness.md#the-guard)):

- `.claude/settings.json`: `allow` (make, read-only git and gh, read/edit of the sources),
  `deny` (reading or editing `.env*`, edits under `design-ref/`).
- `.claude/hooks/guard.py`: a short pattern matcher run before every shell command - blocks
  reading secrets, destructive docker cleanups, force/delete pushes and skipping the git
  hooks. Cases: `.claude/hooks/guard-cases.txt`, run by `scripts/test_guard.py`.
- `.claude/hooks/format.sh`: formats edited Python (ruff) and UI files (prettier) in
  containers.
- **Approval rules**: commit and push need none. Agents merge their own PRs only under the
  [board rules](board.md#merging-and-closing); deployment is human-only and outside
  development. A data-model or large design change starts with a plan (CLAUDE.md).
