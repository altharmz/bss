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
  the tests the change affects, plus UI and generated-docs checks when their sources change.
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
6. Syncs the compose files and `.env.prod` (as `~/bss/.env`).
7. **Runs the migrations** with the new image before the app serves traffic (with a
   deadline).
8. `up` - waits until **`app`, `worker` and `mcp` are healthy** (90 s).
9. Atomically writes the full Git commit to `~/bss/DEPLOYED`, reads it back and
   requires an exact match. The smoke output prints the commit and image tag. The same
   commit goes to the workstation's `.deployed` (gitignored) with tag, subject, last
   migration, UTC time and `checks=running`; after the last step `checks=passed`.
   `make deployed` prints it and the commits since.
   Smoke on the VPS: `/healthz` and `/billing-status` (200 must say `ok` or
   `running`; 503 one of `stale|failed|running|none`; `scripts/billing-status-check.sh`,
   shared with `make smoke`).
10. Rsyncs the built UI **into** `~/bss/web` (hashed assets, then `index.html`, then a
    `--delete` pass; the directory is never replaced, so Caddy's bind mount keeps it).
11. **Web smoke** through Caddy (`scripts/web-smoke.sh`; Caddy asks for no login, the app
    is the boundary): this build's `index.html`, a deep link and a hashed asset without
    credentials, `/internal/v1/context` 401 JSON from the app without them, with the
    read-only smoke secret 200 JSON with `operator` = `smoke`, a TMF list with
    `X-Total-Count`, `POST` -> 403 from the app, `/healthz` open.
12. Runs the MCP discovery, authentication and OAuth smoke checks through Caddy, then the
    **OAuth login smoke** (`scripts/mcp-oauth-smoke.py`) as the `mcp` smoke user from
    `.env.smoke` (`MCP_SMOKE_USER`, `MCP_SMOKE_PASSWORD`): read scope only, one read tool,
    a write tool refused, refresh, revoke. Without those lines it prints SKIPPED. Then it
    prunes the smoke's client registrations in the `mcp` container (a warning on failure).
13. Prunes old `bss-app` images on the VPS, keeping the newest few (nothing else is
    touched).

A deploy is **not fully reversible once a migration shipped**: rolling the image back does
not roll the schema back (`alembic downgrade` by hand). `DEPLOYED` records the backend
that reached service health, even if a later API/web/MCP smoke fails; it does not
certify the entire release gate; `.deployed` with `checks=running` after a deploy
means a later smoke failed.
Production config, limits and the constraints of the VPS (VPN ports, no prune, nothing
published on 0.0.0.0): see [decisions/infra.md](decisions/infra.md). Never run `docker build`
on the VPS.

## Prepaid: recovery and rollback

**Rollback barrier.** Migrations `0034`-`0047` carry the prepaid schema. Each downgrade
refuses to run once the data it would lose exists: `0034` refuses any prepaid account or
bucket (so the first prepaid account created on a deployed instance ends clean
downgrade past `0033`); the later ones refuse retained holds, commands, command runs,
funded intervals and service grants, shortfall observations, manual suspensions,
balance actions, journal keys and transfer-fee charges. Do not delete history to make
a downgrade pass. Rolling the image back never rolls the schema back: an older image
over a newer schema is a manual, reviewed decision, and with prepaid history the
supported recovery is forward (a fixed image), not `alembic downgrade`.

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
removes data. A restore drill from a cron-made dump is an open check (issue #9). There is **no `/backup-status` endpoint** in bss; backup
monitoring, if any, belongs to vps-infra.

## Monitoring

| Signal | Where | Meaning |
|---|---|---|
| `GET /healthz` | app, unauthenticated | 200 when the app can reach the database, else 503 |
| `GET /billing-status` | app, unauthenticated | billing freshness: 200 `ok`/`running`, 503 `stale`/`failed`/`running`(overdue)/`none` ([billing-worker.md](billing-worker.md#billing-status)) |
| `GET /metrics` | app and mcp, bearer token ([ops/monitoring/](../ops/monitoring/README.md)) | Prometheus text format; 404 until `BSS_METRICS_TOKEN` is set. HTTP rate/latency by route template, queue depth and age, billing freshness, product counts (database series cached 30 s, `bss_db_up 0` when the read fails). Worker health shows through the queue and billing series |
| app healthcheck | `Dockerfile` | bash `/dev/tcp` probe of `/healthz` every 60 s |
| worker healthcheck | `compose.prod.yaml` | `/tmp/worker-alive` younger than 180 s |
| logs | `make logs` (local), json-file with rotation on the VPS | 500s are logged with their traceback |

Docker healthchecks are **informational only**: nothing restarts or replaces an
`unhealthy` container (`restart: unless-stopped` reacts to exits). Real alerting is the
external monitor polling `/healthz` and `/billing-status` (configured outside this
repository).

## The agent harness

Every regular operation is a `make` target and agents are told to use them
([worktrees.md](worktrees.md)). The few hard guard rails exist against **agent mistakes, not a
hostile agent** ([decisions/harness.md](decisions/harness.md#the-guard)):

- `.claude/settings.json`: `allow` (make, read-only git and gh, read/edit of the sources),
  `deny` (reading or editing `.env*`, edits under `design-ref/`).
- `.claude/hooks/guard.sh`: a short pattern matcher run before every shell command - blocks
  reading secrets, destructive docker cleanups, force/delete pushes and skipping the git
  hooks. Cases: `.claude/hooks/guard-cases.txt`, run by `.claude/hooks/test-guard.sh`.
- `.claude/hooks/format.sh`: formats edited Python (ruff) and UI files (prettier) in
  containers.
- **Approval rules**: commit and push need none. Agents merge their own PRs only under the
  [board rules](board.md#merging-and-closing); deployment is human-only and outside
  development. A data-model or large design change starts with a plan (CLAUDE.md).
