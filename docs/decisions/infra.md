# Infrastructure: image, layout, conventions, VPS, deploy

Index: [README.md](README.md).

## Environments

The runtime image is about 74 MB compressed (content size) and 317 MB on disk on the VPS,
321 MB locally (checked at `921603f`): Debian base 88 MB, Python 41 MB, `/opt/venv` 112 MB,
app + alembic ~120 kB. Nothing beyond the venv and app code is in it; inside the venv, pip
(13 MB) is not needed at runtime and sqlalchemy (28 MB) is there only for Alembic.

### Base image

`python:3.12-slim` is **pinned by digest** in the Dockerfile (the multi-arch index digest,
like `node:22-alpine` in compose.override.yaml). The floating tag moved once under us and
turned a cached build into an 11-minute rebuild (apt 414 s, pip 221 s). Pinned at
`dddfd7e07f9d` on 2026-10-03: the base `9424b9b` was deployed with (its 4 base layers
match the deployed image's first 4). Bump it on purpose, as its own commit, for a
security fix or a new 3.12 patch release:

1. `docker buildx imagetools inspect python:3.12-slim` - the top `Digest:` is the index.
2. Replace the digest in the `FROM` line (keep the `3.12-slim` tag in front of it for
   readers); `make test` (expect the full ~11 min rebuild once).
3. `make verify` - the image fingerprint changes, so the deploy ships a new image.

## Layout

```
app/main.py               FastAPI app: /healthz and a JSON status root
app/config.py              settings from environment variables (pydantic-settings)
app/db.py                  psycopg 3 connection + the liveness probe /healthz calls
app/clock.py               Clock protocol, SystemClock, FixedClock; the only wall-clock reader
app/repository/            data access: plain functions on a psycopg.Connection, never commit
app/billing/               billing core (model.py dataclasses, core.py pure functions),
                           adapter.py (bill_account: load -> compute -> write) and
                           settlement.py (which credits pay which bills; pure). See "Billing"
                           and "Bill lifecycle"
app/internal/              internal API for the admin UI (/internal/v1): context, first-charge
                           preview, open products, charges, transactions. See "Internal API"
app/api/                   TMF Open API surface: one module per API (router, request models,
                           row -> body mapping) + shared conventions (errors, tmfjson, common,
                           listing, deps). See "TMF API conventions"
spec/                      vendored TMF OpenAPI files (README.md: source, SHA, license);
                           spec/internal/bss-internal.oas.yaml is ours (the internal API)
alembic/                  migrations; alembic.ini reads DATABASE_URL from the env
compose.yaml              base definition: only what both modes share (the app image)
compose.override.yaml     local dev only (own postgres on `lab`, bind mount, --reload, ports;
                          `tools` service: writable mount for make fmt + format hook)
compose.prod.yaml         VPS only (bss-edge/bss-db networks + alias, DATABASE_URL, limits, log rotation)
scripts/deploy.sh         build -> save -> ssh load -> migrate -> up -> healthcheck -> smoke
scripts/smoke.sh          HTTP smoke test against a base URL
scripts/pre-commit.sh     make lint + smoke + affected tests (harness.md); installed via `make hooks`
scripts/pre-push.sh       refuses pushes adding bcrypt hashes / .env* paths, deletions,
                          non-fast-forwards; installed via `make hooks`
frontend/                 admin UI (stage 5): Vite + React + TypeScript; see "Frontend conventions"
design-ref/               design handoff for the UI - read-only reference, never edited
tests/                    pytest suite (conftest.py spins up throwaway databases;
                          apiclient.py + contract.py validate every API response against
                          spec/; billing_sim.py: in-memory ledger for the billing core;
                          test_billing_properties.py: hypothesis);
                          test_health.py and test_migrations.py check the infrastructure
                          this template provides and must keep passing on an otherwise
                          empty service
```

The sections named there: "Billing" - [billing-core.md](billing-core.md); "Bill
lifecycle" - [bills.md](bills.md); "Internal API" - [internal-api.md](internal-api.md);
"TMF API conventions" - [tmf-api.md](tmf-api.md); "Frontend conventions" -
[frontend.md](frontend.md).

`docker compose` picks up `compose.override.yaml` automatically, so plain
`docker compose up` is always the *local dev* stack. Production composition is explicit:
`-f compose.yaml -f compose.prod.yaml`.

Because `compose.yaml` is merged into both, it holds only what both modes share. The
local postgres (`db`, its healthcheck and `pgdata` volume), the app's `depends_on`,
`DATABASE_URL` and the `lab` network all live in `compose.override.yaml`; anything put
back into the base file would also end up in the production stack (a `lab` network there
would attach the prod container to both `lab` and `shared`).

## Conventions

- App listens on **8080** inside the container. Do not hardcode host ports in code.
- **Healthchecks** (1 vCPU on the VPS: no interpreter start per probe). **Informational
  only here**: nothing restarts or replaces an `unhealthy` container (no autoheal;
  `restart: unless-stopped` reacts to exits, not to health), and `deploy.sh` only reads
  them while it waits after `up`. Real alerting is the external monitor on `/healthz`
  and `/billing-status`.
  - **app** (Dockerfile, `dev` and `runtime`): bash `/dev/tcp` to 127.0.0.1:8080, `GET
    /healthz HTTP/1.0`, the status line must contain ` 200 `; exec form with `bash`
    (`/bin/sh` is dash, no `/dev/tcp`); `read -t 2` inside the 3 s timeout.
  - **worker** (`compose.prod.yaml`): `[[ -n $(find /tmp/worker-alive -mmin -3) ]]`
    (`$$` in the file). GNU find 4.10 compares `-mmin` exactly, no rounding: 179 s
    passes, 181 s fails, missing fails.
  - **One-off runs** (`make test`): the `oneoff` service in `compose.override.yaml` is `app`
    with `healthcheck: disable: true` (nothing serves `/healthz` while pytest runs); `app`
    keeps its check because `make up` waits on it.
  - Both: interval 60 s, timeout 3 s, retries 3, start period 30 s with `start_interval`
    2 s (Engine 25+ / Compose 2.20.2+; the VPS and WSL run Engine 29.7.2, Compose
    v5.5.0). A container is healthy ~2 s after start; a failure shows as `unhealthy`
    after 3 probes, ~3 min. On the VPS (`compose.prod.yaml`) the app's start period is
    180 s and mcp's 120 s: both start slower than 30 s there (0.5 / 0.25 CPU, starting
    together), and after the start period the next probe comes only 60 s later - past
    deploy.sh's old 90 s wait (2026-10-18: a deploy failed with the app "starting" and its
    first passing probe just after the wait; 2026-10-20: the same with mcp still
    probing its routes at startup). deploy.sh now waits 240 s = the longest start
    period (180 s) + one probe interval (60 s).
  - Proven locally (2026-10-05): the app server killed (port still listening) -> probe
    exit 142 (read timeout), `unhealthy` after 3 probes, healthy 2.7 s after a restart;
    `/healthz` returning 500 -> exit 1, `unhealthy`, healthy again at the next probe once
    restored, no restart; `up --wait` healthy at +2.3 s. Worker check from the container
    compose created out of `compose.prod.yaml` (the engine gets one `$`): ages 0-179 s
    exit 0, 181/185/200 s and missing exit 1; healthy at +2 s, `unhealthy` after 3
    probes with a 200 s old file, healthy at the next probe once fresh; with `-mmin -4`
    the 181-200 s files passed. `docker compose config` prints `$$` re-escaped - not what
    the engine receives.
- Config comes from environment variables only (see `app/config.py`). No config files.
- `/healthz` must stay cheap and must verify the DB connection — Docker's HEALTHCHECK
  and the deploy script both depend on it.
- Python 3.12, `ruff` for lint and format, `pytest` for tests.
- `make test` runs the suite in parallel (`pytest -n auto`, pytest-xdist). Tests share
  nothing: each worker migrates its own template database and every test gets its own
  clone, dropped afterwards. A run cut short (a killed hook) cannot drop its own, so
  the names carry their creation time (`tests/scratch_db.py`) and every test session
  first drops the `vpslab_%` databases older than 6 hours that nobody is connected to
  (2026-10-23: 37 had piled up). Hypothesis
  examples per property come from a profile selected by `HYPOTHESIS_PROFILE`: `dev` (50)
  for `make test` and the pre-commit hook, `ci` (200) for `make verify` (a target-specific
  variable in the Makefile). Plain `pytest` without the variable uses `dev`.
  **Workers stay at `-n auto`** (one per core, 12 on the workstation): measured
  2026-10-20, 12 workers already keep the CPU ~91% busy, and `-n 24` saturated it,
  pushed the WSL VM into swap and made postgres fail the concurrent template migrations
  with `out of shared memory` (its lock table). The `api` fixture's TestClient runs
  inside `with`: one event-loop thread per test instead of one per request.
- **The local postgres runs with `fsync=off`, `full_page_writes=off`,
  `synchronous_commit=off`, `max_locks_per_transaction=256`** (`command:` of `db` in `compose.override.yaml`): every test
  clones and drops a database, and with fsync on each drop waits for a server-wide
  checkpoint. Local only - production uses vps-infra's postgres and never reads
  `compose.override.yaml`, so it is unaffected. Not crash-safe: after a WSL crash (or a
  killed Docker VM) the local database may be corrupt; the recovery is `make clean`
  (drops the local volume) and `make up`. The lock limit (default 64) is for the test
  templates: every xdist worker migrates its own template in one transaction and the lock
  table is shared, so near 62 tables the parallel suite ran out of shared memory (issue #186).
- Spec YAML is parsed with libyaml (`yaml.CSafeLoader`), not `yaml.safe_load`: same
  documents, ~6x faster, and every xdist worker parses all of `spec/` once.
- **Dependencies are pinned, never floating** (since 2026-10-03). Direct dependencies are
  `==` in `requirements.txt` (runtime) / `requirements-dev.txt` (tooling); every package,
  transitive ones included, is frozen in `constraints.txt` / `constraints-dev.txt`, and
  the Dockerfile installs with `-c` and pins pip itself (`pip==26.2.1`). Proven: the
  rebuilt dev image's `pip freeze` equals the one that passed lint and tests, and a
  runtime build's equals production's `9424b9b` (28 packages). **The next deploy ships a
  new image** even without code changes: the builder layer was rebuilt (same packages,
  new layer digests), so the deploy fingerprint differs. **To change a version:** edit
  the `==` pin (or add the dependency), install it in a throwaway container, regenerate
  the constraints file from that container's `pip freeze` deliberately, review the diff
  of the constraints file, rebuild, `make lint && make test` - one commit. Never loosen a
  pin to `>=`. Do not `pip install` inside a running container.
- **Migrations already on `master` are immutable.** A fix to one - schema, data or
  comment text - goes into a new migration. Alembic records only the revision id, so a
  database already at that revision never sees an edit: it silently differs from a fresh
  one. One exception happened before this rule: `0005` (comments only) was edited once
  on master before its first deploy (`cloud/catalog-integrity`, the
  `billing_run.started_at` comment), so the local stack, migrated to `0005` before the
  edit, still has the old comment text; production gets the edited one.
- Before every commit: the pre-commit hook (`make lint`, smoke and affected tests); the
  full `make test` before a PR that touches billing, migrations or the API. Small, focused
  commits.

## DB connection pool

**Decided 2026-10-20:** the API takes its connections from a pool (`app/db.py`,
`psycopg_pool`): at most `DB_POOL_SIZE` (default 10) per process, one kept open; a
request waits up to `DB_CONNECT_TIMEOUT` (10 s) for a free one. This replaces "a
connection per request; a pool only after measuring on the VPS" (history below).

- **Why:** measured locally, a request spent most of its time connecting. Each request
  opened two connections (the session check in `app/auth.py`, then the handler), each a
  TCP connect, a SCRAM handshake and a new backend: 69 ms for a request the app refuses
  at once, 8.4 ms pooled. `make test` (~1450 tests, mostly API requests) took 8-8.5 min.
  Production saves the same per request and gets the bound the open check asked for.
- **Budget:** postgres on the VPS has 37 usable connections; bss takes at most 10 (API)
  + 1 (worker) + a migration or `pg_dump`. Before, the threadpool's 40 sync requests
  could ask for 40.
- **Same semantics as a fresh connection:** `connection()` uses `getconn`/`putconn`,
  not the pool's context manager (which commits on exit); whatever the caller left
  uncommitted or failed is rolled back before the connection goes back, as closing it
  did. Nothing in `app/` keeps session state: its advisory locks are `_xact_` ones, no
  `SET`. The worker's session advisory lock is on its own connection
  (`app/worker/db.py`), not pooled.
- **Database restarts** (nightly reboot, a db restart): `check=` tests a connection
  before handing it out and replaces a dead one. `tests/test_db_pool.py` proves reuse,
  rollback of leftovers, and replacement after `pg_terminate_backend`.
- **Tests:** pools are keyed by URL, so each test database gets its own; conftest's
  `_drop_database` closes it before the drop (else its workers would reconnect to a
  dropped database). `--reload` restarts the process, so no pool outlives a reload.

History - the open check before the pool:

- **App opens a new DB connection per request (`connect_timeout` was 3 s, now 10 s)** -
  open: how to bound the API's connections on prod (1 vCPU, shared with VPN/Xray).
  Reproduced locally (2026-10-03, with 3 s): `docker pause bss-db-1` for 6 s during `make
  test` gave 6 failed + 2 errors, each `assert response.status_code == 201` on a 500
  "internal error"; a request during the freeze answered 500 after exactly 3.01 s, and a
  direct connect raises `psycopg.errors.ConnectionTimeout: connection timeout expired`.
  Only a connect that starts more than 3 s before the db recovers fails; connections
  without a timeout (the test fixtures') just wait.
  **Decided (stage 4 planning):** the API keeps a connection per request with
  `connect_timeout` raised to 10 s (done: `app/config.py`); a pool only after measuring
  on the VPS.
  The worker holds one dedicated long-lived connection (advisory lock and work on the
  same connection, TCP keepalives, `connect_timeout` 10 s). Budget measured 2026-10-03
  (read-only, `make remote`): `max_connections` 40, 3 reserved for superusers -> 37; no
  idle client sessions at the time. Expected peak ~10 (a few API requests, the worker, a
  migration, `pg_dump`). **Not bounded:** sync endpoints run in FastAPI's threadpool (40
  threads), each in-flight request opening its own connection, so a burst could make bss
  alone ask for more than 37 - the concrete argument for a pool (max N) later. A cheaper
  alternative to a pool: **cap concurrent DB connections from the API** - a semaphore
  around connect, or a smaller threadpool limit (e.g. 10) - which bounds the worst case
  without a pool's lifecycle issues (`--reload`, per-test databases). Superseded: the pool above was built.

## Shared infrastructure (vps-infra)

On the VPS, Postgres and Caddy are **not ours**. The separate vps-infra project is
deployed once per host and runs one Postgres and one Caddy for every app there, owning
their config, data, TLS and backups. This repo only ships the app container.

- **Network**: bss is not on `shared` (every app's container could reach `bss:8080` and
  `bss-mcp:8080` there, bypassing Caddy). vps-infra creates two per-app networks:
  `bss-edge` (Caddy, app, mcp) and `bss-db` (Postgres, app, worker, the migration
  container). `mcp` has no DB and `worker` serves no HTTP, so each is on one only. Both
  must exist before `make deploy`; `scripts/deploy.sh` checks for them and fails early with
  "deploy vps-infra first" otherwise. vps-infra is never needed locally — do not start it
  for development.
- **Alias**: on `bss-edge` the app is reachable as `bss` (network alias in
  `compose.prod.yaml`). Every app there names its service `app`, so that name is
  ambiguous; always use `bss`. The MCP connector (`mcp`, [mcp.md](mcp.md)) is
  `bss-mcp:8080` there and calls the app as `bss:8080`.
- **Database**: `DATABASE_URL` on the VPS is `postgres:5432/bss`, role
  `bss`; `postgres` is the service name in vps-infra, and the database + role are
  created there with `make add-db NAME=bss`. The password is `POSTGRES_PASSWORD`
  from `.env.prod`, which must equal the role password `make add-db` used (`DB_PASSWORD_<NAME>` in
  vps-infra's `.env.prod`) — the value from the first
  setup is temporary, and a mismatch makes migrations hang until the 120s timeout
  instead of failing clearly. Migrations still run from this repo (`deploy.sh`, before the app
  starts).
- **Reverse proxy**: this app's site block (TLS + basic auth on `8445`,
  `https://vpn2811.duckdns.org:8445`, `/healthz` carved out without auth for external
  monitoring) is added **by hand** to vps-infra's Caddyfile and proxies to
  `bss:8080`. Routing, auth and TLS changes are made there, not here. `/healthz`
  must still stay cheap, check the DB and return nothing beyond status — the external
  monitoring depends on it.
- **Backups**: vps-infra dumps all databases, this one included. This project has no
  backup or restore tooling. Make sure a fresh vps-infra dump exists before any migration
  that alters or removes existing data.
- The VPS reboots nightly at 03:00 (root cron). Everything must come back on its own —
  never rely on manually started containers.

## Known compose pitfalls (found on live deploys)

Both of these only showed up against the real VPS, never locally:

- **`docker compose run --name ...` hangs** instead of starting the container. Don't use it
  to get a predictable name for the one-off migration container; `deploy.sh` finds a
  leftover one by compose's labels (`com.docker.compose.project=bss` +
  `com.docker.compose.oneoff=True`) instead.
- **`--quiet-pull` hangs when the image was only loaded locally.** The image reaches the
  VPS via `docker save | docker load`, with no registry behind it; with `--quiet-pull`
  compose tries to check that nonexistent repository and never returns. Keep it off every
  remote compose call.

## Deploying the UI (stage 5, checkpoint 7)

The Caddy side is vps-infra's. What bss relies on (applied; vps-infra owns the config):

- `~/bss/web` on the VPS (the deploy user's home) holds the UI, written only by
  `deploy.sh` with rsync *into* the directory, never replaced or renamed. vps-infra mounts
  it read-only into Caddy (`/srv/bss-web`) and creates it before Caddy starts with the
  mount (else Docker creates it root-owned and rsync cannot write).
- Routes: `/healthz` and `/billing-status` are open (the external monitor); `/tmf-api/*`
  and `/internal/*` reach `bss:8080` (a missing `/internal/*` makes `/internal/v1/context`
  answer `index.html` with 200, which is why the web smoke checks for JSON); everything
  else is the static UI with `try_files {path} /index.html`, `index.html` `no-cache` and
  `/assets/*` immutable.
- `X-Remote-User` is stripped on every route, never set; `X-Forwarded-For` is Caddy's
  default (the sign-in throttle keys on its last entry, `APP_TRUST_FORWARDED`). On the
  `bss2811` hostname requests pass the host nginx first, so all sign-ins there share one
  throttle bucket (accepted). Caddy asks for no login since U11 ([users.md](users.md)).
- Order of a first deploy: fresh backup, vps-infra's mount and site block, then
  `make verify`. If the web smoke fails, the app is already deployed and healthy (the UI
  ships last); fix the Caddy side and re-run `make deploy`.

**First web deploy: done** - `9424b9b`, 2026-10-02, `make verify` end to end (migration
0003 ran, web smoke 8/8 through Caddy).

- **`deploy.sh`** builds the UI locally before any network step (the primary-only
  `./scripts/task-compose --deploy-web-build <tag>` uses the offline container with fixed,
  nonsecret settings and stamps `index.html`'s `bss-build` marker; normal development builds
  remain task-only; `./scripts/task-compose --deploy-web-install`, `make deploy-web-install`,
  runs `npm ci` there when the lockfile changed) and refuses to start without
  the web smoke's credential in **`.env.smoke`** - a local-only file, gitignored and never
  shipped. `.env.prod` is copied to the VPS as `~/bss/.env` (compose reads it only to fill
  `${...}`; the container gets just the variables listed under `environment:`, there is
  no `env_file`). The credential was Caddy's `SMOKE_BASIC_AUTH=smoke:<password>` (and
  deploy.sh refused it in `.env.prod`); since U11 (2026-10-19, Caddy asks for no login)
  it is only the app's `BSS_SMOKE_SECRET` ([users.md](users.md#after-u11-the-plan-slice-4-2026-10-19)).
  `.env.smoke` is denied to reads like `.env`/`.env.prod`
  (settings.json, guard). **`DRY_RUN=1 ./scripts/deploy.sh`** runs every local check and
  lists exactly what the rsync calls would ship (the same `ship_config`/`ship_web`
  functions, `--dry-run` into a scratch directory), proves `.env.smoke` is not among it,
  and stops before any network step. After the app is healthy it rsyncs
  `frontend/dist/` **into** `~/bss/web` - hashed assets, then `index.html`, then a
  `--delete` pass. The directory is never replaced or renamed (the bind mount holds its
  inode); rehearsed locally: same inode across two deploys, stale assets removed. The UI
  ships last, so it never calls an API the old app lacks.
- **`scripts/web-smoke.sh <url> <build>`** then checks the public URL (since U11 Caddy
  asks for no login; the smoke secret goes to the app as `X-Bss-Service` via a curl
  config on a file descriptor, never argv or output): `index.html` of this build, a deep
  link answered by `index.html` and a hashed asset, all without credentials;
  **`/internal/v1/context` 401 JSON from the app without them** (not the index.html
  fallback), and with the secret 200 JSON with `operator` = `smoke`; a `/tmf-api` list
  as JSON with `X-Total-Count`; `POST` with the secret -> 403 from the app; `/healthz`
  open. A failure there leaves the app deployed and healthy; fix the Caddy side and
  re-run `make deploy`.
- **`make web-e2e`**: Playwright (official image, pinned by digest, same version as
  `@playwright/test`) against the **built** UI served by `vite preview` (`web-preview`
  service, proxying to the local app) and the seeded stack (`make seed` first). Read-only
  and data-tolerant (it picks any offering priced today: the local catalog is also edited
  in manual reviews); fails on any console error, page error or 5xx. Part of `make verify`
  - the gate for API changes that break the UI.

## Running commit record (2026-10-20)

Authorized scope: record deployment identity, independently of the progress
tracker's future per-item deployed coverage. The human approved the exact patch and
applied it after the protected-file edit hook continued to block agent application.

After app, worker and MCP health checks pass, deploy.sh atomically replaces
`~/bss/DEPLOYED` with the full Git HEAD used for the clean build. The remote smoke
reads it back, requires an exact match and prints the commit alongside the image tag.
The commit is independent of an optional TAG override. A failed write/read or mismatch
fails deployment. Dry runs and failures before service health do not update the file.
Later API/web/MCP smoke failures leave the record of the newly running backend intact;
the marker records running backend identity, not success of the entire release gate.
No local deployed coverage file is written and no coverage percentages change
(superseded 2026-10-21, below).

Local validation: Bash syntax check passes. Isolated execution of the applied
record/read-back block passes an actual atomic write and read (including a custom
image tag) and fails on mismatched content, a failed write or a failed read. No network calls or
deployment were performed. The record contains exactly the full commit plus a newline,
and the temporary file is gone after the rename. Production confirmation awaits a
separately approved deployment.

`make lint`, `make test` (1,405 passed) and `make decisions-check` pass. Corrected
API notes were regenerated with `make docs`; a second generation left all 53 reference
pages identical. Scope is unchanged.


## Local deploy record (2026-10-21)

The human: keep track of the deployed version in a file that only `make deploy` updates.
`scripts/deploy.sh` writes `.deployed` in the workstation's repo (gitignored, never
committed - a commit per deploy would make HEAD differ from what runs) right after the
remote `DEPLOYED` matches: `commit`, `tag`, `subject`, `migration` (newest file in
`alembic/versions/` of that commit, from git, not the working tree), `deployed_at` (UTC)
and `checks=running`; after the last step it is rewritten with `checks=passed`. Written
via a temp file and `mv`. `make deployed` prints it and `git log <commit>..HEAD`. Agents
never write it: Claude's Edit deny rule, `.codex/patch-guard.sh` and a guard.sh rule
against shell writes (cases in `guard-cases.txt`). The VPS's `~/bss/DEPLOYED` stays the
authority; `.deployed` is the workstation's copy of the last deploy made from it.

## Metrics (2026-11-07)

Issue #240. An external monitor (Prometheus, Grafana Cloud, VictoriaMetrics, Datadog's
OpenMetrics check) scrapes `GET /metrics` on the app and on mcp; the kit is in
[`ops/monitoring/`](../../ops/monitoring/README.md).

- **Library:** `prometheus-client`, pinned like every dependency; one non-default registry per
  service plus the process and platform collectors.
- **Endpoint and access:** not in the OpenAPI schema (so no MCP tool, no `docs/api`, no scope
  item). Off (404) unless `BSS_METRICS_TOKEN` is set, the same value on app and mcp; then a
  bearer token compared with `hmac.compare_digest`, else 401. The app's check lives in
  `authenticate` (`app/auth.py`), so the route-protection test covers it; `/metrics` is not in
  `OPEN`.
- **HTTP series (app):** one pure ASGI middleware; labels are the method, the matched route
  template (`unmatched` otherwise) and the status - never an id, a raw path or an amount.
  `/metrics` and `/healthz` are excluded. The duration uses `time.perf_counter()`: a
  monotonic duration is not a read of the wall clock.
- **Database series (app):** a custom collector reads queue depth and age, billing freshness
  (`app/status.py`'s logic) and product counts in one read-only transaction with a 2 s
  statement timeout, cached for 30 s with `app/clock.py`. On any error only `bss_db_up 0` is
  exported: never stale values. The queue-to-status mapping is one dict in `app/metrics.py`.
  `bss_prepaid_lines` is not exported: no prepaid lifecycle state comes from one indexed query.
- **Worker:** no endpoint (no HTTP server there); its health shows in the queue and billing
  series. No pushgateway.
- **MCP series:** fed from `bss_mcp.audit.audit`, so the audit log and the counters agree. The
  duration histogram has no tool label; OAuth results are a bounded set.
- **Build:** compose passes `BSS_BUILD: ${TAG:-dev}` to app, worker and mcp; `scripts/deploy.sh`
  is unchanged.
- **Exposure on the VPS is the human's step:** the Caddy route in vps-infra, the token in
  `.env.prod`, the scraper.

## Automated rollback (2026-11-08)

Issue #238. Before it a failed deploy left the new release in place and the server kept no
old UI or compose files. Now `scripts/deploy.sh` (still the only script that changes the
VPS) keeps the last two releases and goes back one by itself. Operator view:
[operations.md](../operations.md#rollback).

- **A release** is its image tag (short commit). `~/bss/releases/<tag>/` holds
  `compose.yaml`, `compose.prod.yaml`, `web/` (the UI files shipped) and `release.env`
  (`tag`, `commit`, `alembic_rev` = the `revision` of the newest file in `alembic/versions/`
  at that commit, `deployed_at`). `.env` is not copied: one `~/bss/.env`, and compose
  ignores variables an older release does not use. **Why:** there is no registry; the
  image is `docker load`ed, so the server itself must keep what an old release needs.
- **Pointers** `~/bss/CURRENT` and `~/bss/PREVIOUS` (one tag each, temp file + `mv`). They
  move only after a deploy's last smoke; re-deploying `CURRENT` keeps `PREVIOUS`.
- **Retention:** the directories and images of `CURRENT` and `PREVIOUS` (plus the one being
  deployed). The image prune keeps the 3 newest and never removes either pointer's image;
  it removes by `repo:tag` (dangling ones by ID).
- **Point of no return** is `compose up -d` of the new release (`ROLLBACK_ARMED`). Any
  `fail` after it rolls back, once (`fail` clears the flag first). Before it nothing that
  serves has changed: alembic runs all pending migrations in one transaction
  (`alembic/env.py`), so a failed upgrade leaves the schema as it was. Warnings (the smoke
  client prune, image and release pruning) never roll back.
- **Target:** an automatic rollback returns to `CURRENT` (still the old release, since the
  pointers have not moved), or to `PREVIOUS` when the deploy was a re-deploy of `CURRENT`;
  `make rollback` goes from `CURRENT` to `PREVIOUS`. Either way `CURRENT` becomes the target
  and `PREVIOUS` is deleted: **one step only**, a second rollback says "deploy instead".
- **Schema:** when the two `alembic_rev` differ, `alembic downgrade <target rev>` runs in
  the image being left - only it contains the downgrade scripts - with the 120 s deadline
  and the one-off cleanup of the upgrade. A refusal is never forced: that release is
  brought back up and the script stops with "ROLLBACK REFUSED". **Why:** lossy downgrades
  refuse on purpose ([operations.md](../operations.md#prepaid-recovery-and-rollback)), and
  this repo has no restore tooling (vps-infra owns the dumps).
- **UI:** `~/bss/web` is synced on the server from `releases/<tag>/web/` with the deploy's
  three passes (assets, `index.html`, `--delete`) and remote `rsync -a`; the deploy ships
  the UI to its release directory first, so `web/` always equals a stored release. The
  bind-mounted directory is still never replaced.
- **Smokes after a rollback:** deployed commit, `/healthz`, `/billing-status`, web smoke
  with the restored build, MCP smoke; not the OAuth smoke (the slowest; the MCP smoke
  already proves the service). `.deployed` gets `checks=rolled-back` and
  `rolled_back_from=<tag>`.
- **Bootstrap:** without `CURRENT` but with a running app container, the first deploy
  records the running release (`docker inspect` tag, compose files and `web/` from `~/bss`,
  commit from `DEPLOYED`, `alembic current`) before shipping anything, and makes it
  `CURRENT`. Without one there is nothing to roll back to, and the log says so.
- **`make rollback`** = `deploy.sh --rollback`: no build, ship or clean-tree check;
  `.env.smoke` is still required for the smokes. `DRY_RUN=1` prints the plan and changes
  nothing.
- **Drill:** `BSS_DEPLOY_DRILL` (a migration failure or overrun, a really unhealthy release,
  a failure after each gate, failing cleanup) and `BSS_ROLLBACK_DRILL=slow-downgrade` make
  every rollback path happen on the real VPS ([operations.md](../operations.md#rollback)).
  **Why** (2026-11-08, #254): the human wants every scenario verified live, not only by the
  fake-binary tests. The migration drill acts in `alembic/env.py` after the migrations ran
  but inside their transaction, so it proves the one-transaction claim; the unhealthy drill
  reaches the app as `BSS_HEALTH_DRILL` through compose interpolation on `up` only, so a
  rollback target never inherits it; a failure point fires only while the deploy is armed,
  so the rollback's own smokes never trip it. Drill-only migration `0064` (table
  `rollback_drill`, refusing downgrade while it has rows) gives the drill a schema
  downgrade; `0065` drops it afterwards.
- **Tests:** `scripts/test_deploy_rollback.py` runs the real script with fake `ssh`,
  `rsync`, `docker`, `timeout` and `curl` that log their argv and answer from a rules file,
  and asserts on the order of the remote commands (in `make runtime-check`).
