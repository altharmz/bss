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
match the deployed image's first 4). **Bumped to `5e2bdb1b0d54` on 2026-11-08** (#263;
Python 3.12.16, image built 2026-11-05): fixes Trivy's apt/libapt-pkg7.0 and libpcre2-8-0
findings. zlib1g is still `+b1` there, so its two CVEs stay in `.trivyignore`, renewed to
2027-01-07; the next bump should clear them. Bump it on purpose, as its own commit, for a
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
  nothing: every test gets its own clone of a template database at migration head,
  dropped afterwards. The template is built once for a set of migrations and kept
  (`vpslab_kept_template_<digest of alembic/ and alembic.ini>`): the first worker that
  asks builds it under a server-wide advisory lock, under a timed name that becomes the
  kept one only when the migration has finished, and every other worker and every later
  run finds it. A changed migration set has another digest, so it builds its own and drops
  the older kept ones. Before #319 each xdist worker migrated its own template at start:
  4.5 s in one process, 9 to 30 s each with twelve at once, in every pytest run (the
  commit hook starts two). A run cut short (a killed hook) cannot drop its own, so
  the names carry their creation time (`tests/scratch_db.py`) and every test session
  first drops the `vpslab_%` databases older than 6 hours that nobody is connected to
  (2026-10-23: 37 had piled up), never a kept template. Hypothesis
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
  (drops the local volume) and `make up`. The lock limit (default 64) dates from
  the time every xdist worker migrated its own template in one transaction, all at once:
  the lock table is shared, so near 62 tables the parallel suite ran out of shared memory
  (issue #186). One build at a time needs less, but the limit stays: tests migrate
  databases of their own in parallel too.
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
  pin to `>=`. Do not `pip install` inside a running container. A Dependabot security
  PR is a trigger for this procedure, never a shortcut past it: it bumps only
  `requirements*.txt`, so it is redone in a task branch ([harness.md](harness.md),
  "Supply-chain scanning").
- **No pip in the runtime venv** (decided 2026-11-08, #263). The `runtime-venv` stage
  uninstalls pip from the builder's `/opt/venv` before the runtime stage copies it:
  production never runs pip, and pip vendors its own certifi, msgpack and urllib3 (and
  lists setuptools in `vendor.txt`), which Trivy reported. Bumping pip alone would not
  do: pip 26.3 still lists `setuptools==70.3.0`. The dev image keeps pip (it installs
  the tooling); the base image's own `/usr/local` pip is untouched and Trivy did not
  report it. Nothing in `app/` or `bss_mcp/` imports pip.
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
- **Logging** (issue #273; `app/logsetup.py`, used by the app, the worker and MCP).
  - JSON lines on stdout, stdlib only, one format everywhere (`make logs | jq`). Fields:
    `ts` (UTC, ms, from logging's own timestamp), `level`, `logger`, `msg`, `process`
    (`app`/`worker`/`mcp`), `request_id` when set, `exc` (traceback), and the whitelisted
    extras `method`, `route`, `status`, `duration_ms`, `operator`.
  - Levels: root at `WARNING` with the one handler; `OUR_LOGGERS` (`app`, `bss_mcp`,
    `uvicorn.error`) at `LOG_LEVEL`. Third-party loggers (`psycopg`, `httpx`, `asyncio` ...)
    inherit `WARNING` and are never raised: their DEBUG output can carry SQL parameters and
    headers. uvicorn's handlers are removed and `uvicorn.access` is disabled.
  - `RequestLogMiddleware` (pure ASGI, outermost) takes an inbound `X-Request-Id` only if it
    matches `^[A-Za-z0-9._-]{1,64}$`, else makes `uuid4().hex`; returns it on every response
    (the 500 handler adds it itself: Starlette sends that response from outside the
    middleware); logs one line per request (INFO, 5xx ERROR, `/healthz` and `/metrics` at
    DEBUG). Never the query string, headers or body.
  - The ID is a `ContextVar`, read by a handler filter, so every line of the request carries
    it. It is not reset after the request (each request is its own task; the 500 handler runs
    after the middleware has left). A worker wake-up sets `wake-<8 hex>`. MCP runs the same
    middleware and `Bss` forwards the ID as `X-Request-Id`, so a tool call and its app request
    share one ID.
  - Redaction: no code passes headers, cookies, bodies, passwords or tokens to a log call.
    `RedactFilter` is the net under that rule: it masks `Authorization`, `Cookie`,
    `Set-Cookie`, `X-Bss-Service`, `Bearer <x>` and `password|token|secret|pin|code=<x>`
    in the message, string extras and tracebacks (`[redacted]`).
  - **Runtime level** (issue #274). `effective = DEBUG` while `now < log_setting.debug_until`
    (half-open: at the instant it is over), else `base_level` (`WARNING`/`INFO`), else the env
    `LOG_LEVEL`, which is also the level until the first successful poll and whenever the
    database cannot be read. DEBUG exists only as `debug_until`, at most **120 minutes**
    ahead (enforced by the endpoint: a CHECK cannot express a duration relative to the
    change time), so it always expires by itself. `log_setting` is a singleton like
    `prepaid_lifecycle_setting`; `log_setting_change` is the append-only audit (who, old and
    new base level and expiry, when). Time is the injected clock.
  - **Polling every 15 s** (`LOG_POLL_SECONDS`, 0 = never; the tests set 0), one `LevelPoller`
    (`app/logsetup.py`) that calls `setLevel` on `OUR_LOGGERS` only: the app (a daemon thread
    in the lifespan, own autocommit connection), the worker (the same thread, independent of
    the tick, so a long billing run still sees a change) and MCP (an anyio task in its
    lifespan: it has no database, so it reads `GET /internal/v1/service/log-level`, which
    answers `{baseLevel, effectiveLevel, debugUntil}` and computes its own effective level).
    A failed poll keeps the level, except that an expired `debug_until` still drops to the
    base by the process's own clock; one WARNING per outage, one per level change and process.
    Third-party loggers are never raised: #273's redaction test is re-run with DEBUG raised
    through the database row.

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
  before handing it out and replaces a dead one. `tests/ops/test_db_pool.py` proves reuse,
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
  lists exactly what the rsync calls would ship (the same `ship_env`/`ship_release`
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
  as JSON with `X-Result-Count` (no `X-Total-Count` since #312); `POST` with the secret -> 403 from the app; `/healthz`
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
never write it: this is a rule for the agent; Claude's Edit deny rule and
`.codex/patch-guard.sh` (for Codex patches) block file edits, and no hook checks shell writes. The VPS's `~/bss/DEPLOYED` stays the
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
- **Grafana Cloud rules (2026-11-23, issue #444):** the live rules are
  `ops/monitoring/grafana-cloud-rules.yaml`, written by `make grafana-rules-apply` (the human's
  step; the provisioning API with `X-Disable-Provenance`, merged onto the live group so
  unmanaged fields stay). The Prometheus import had made missing data a recovery, so the
  nightly reboot resolved and re-fired alerts. Decided: no data keeps the last state, errors
  raise an Error alert, queries return raw values and the threshold expression compares (an
  empty result then only means missing series), absence rules (no data = OK) report a scrape
  missing for 15 minutes. Not chosen: `keep_firing_for` (it delays genuine recoveries too), a
  03:00 silence (it hides real failures), threshold changes.

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
- **`~/bss/compose*.yaml` always match the running release** (2026-11-15, #256). The deploy
  ships the new compose files only to `releases/<tag>/`; the migration one-off reads them
  there (`docker compose --project-directory . -f releases/<tag>/compose.yaml -f
  releases/<tag>/compose.prod.yaml`, so `~/bss/.env` still fills `${...}` and the project
  stays `bss`), and they are copied into `~/bss` only by `activate_release`, right before
  `up`. **Why:** the live drill (#254) found the new release's files in `~/bss` after a
  failed migration, while the old release kept serving - `make remote` or a manual compose
  call would have read files that did not match it. Not syncing early was chosen over
  restoring in the failure path: a restore is one more remote step that can itself fail
  (the SSH link is often why a deploy fails). `~/bss/.env` is still synced before the
  migration (it needs the database password) and is not per release, as above.
- **Target:** an automatic rollback returns to `CURRENT` (still the old release, since the
  pointers have not moved), or to `PREVIOUS` when the deploy was a re-deploy of `CURRENT`;
  `make rollback` goes from `CURRENT` to `PREVIOUS`. Either way `CURRENT` becomes the target
  and `PREVIOUS` is deleted: **one step only**, a second rollback says "deploy instead".
- **Schema:** when the two `alembic_rev` differ, `alembic downgrade <target rev>` runs in
  the image being left - only it contains the downgrade scripts - with the 120 s deadline
  and the one-off cleanup of the upgrade. A refusal is never forced: that release is
  brought back up and the script stops with "ROLLBACK REFUSED". A downgrade that overruns
  the deadline gets the same stop but says it did not finish and was rolled back (one
  transaction, schema unchanged), pointing at the database rather than the data. **Why:** lossy downgrades
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
  downgrade; `0065` dropped it once the drill passed (2026-11-08, #259). A later drill
  that needs a schema downgrade adds a new drill-only migration the same way.
- **Tests:** `scripts/test_deploy_rollback.py` runs the real script with fake `ssh`,
  `rsync`, `docker`, `timeout` and `curl` that log their argv and answer from a rules file,
  and asserts on the order of the remote commands (in `make deploy-check`).

## Smoke retries (2026-11-08)

Issue #257. In the live rollback drill (#254) 3 of about 15 deploys rolled back a healthy
release because one request of a public smoke hung - a TCP or proxy connect timing out
after 15 s, once a TLS handshake - while every other request passed and the same checks
passed a minute later. The SSH-run smokes never failed; the workstation reaches the public
URLs through a local HTTP(S) proxy. A rollback for a single stalled connection costs more
than it protects.

- `scripts/smoke-retry.sh` (sourced by `web-smoke.sh` and `mcp-smoke.sh`) and `send()` in
  `mcp-oauth-smoke.py` try a request again - 3 attempts, 3 s apart, each retry logged -
  only when it got **no answer** and repeating it is **safe**: a GET, or a request that
  provably never reached the server (curl `time_pretransfer` 0; in Python a refused
  connection, a failed proxy tunnel or a TLS handshake error).
- **Never retried:** an HTTP status (a wrong answer fails at once), and a POST that may
  have been sent - the OAuth code exchange and the refresh rotation are single-use, so a
  repeat could only fail differently or hide a double effect.
- **Tests:** `scripts/test_smoke_retry.py` (the helper, with a fake request) and
  `RetryTests` in `scripts/test_mcp_oauth_smoke.py`.

### What stalls (2026-11-15, #261)

Measured from the workstation with **no deploy running**, replaying the MCP smoke's 17
requests (one new connection each, 15 s timeout):

| Path to `bss2811.duckdns.org:443` | Timed out |
| --- | --- |
| via the local proxy, 3 runs of the sequence | 9 of 51 |
| via the local proxy, `GET /MCP` only | 1 of 51 |
| via the local proxy, tunnel to the address instead of the name | 9 of 51 |
| direct (`--noproxy '*'`), 3 runs of the sequence | 0 of 51 |

- **Not the deploy.** The stall needs no restart and no load on the VPS; the smokes only
  met it during deploys because that is when they run.
- **Not `/MCP`.** The stalled requests were spread over the whole sequence, GET and POST.
- **Not the proxy's listener and not the proxy as such.** 120 of 120 bare TCP connects to
  `127.0.0.1:10808` took under 6 ms, and 40 of 40 requests through it to an unrelated site
  passed. Every stall is in the tunnel set-up (`CONNECT`): curl exit 28, no TLS started.
- **Not name resolution.** 60 of 60 local lookups were fast, and the tunnel stalls as often
  when the proxy is given the address.
- **So:** it is the proxy's own outbound connection to the VPS address on 443 that
  sometimes gets no answer; a direct connection through the host's SNI router does not.
  Stalls come in bursts (9 of 51, then 1 of 51 a few minutes later).
- **Not measured:** whether port `8445` through the proxy stalls too, and where the proxy
  exits (if it exits on this same VPS, the request loops back to the host's own public
  address). Both mean probing the VPN, which is the human's.
- **What the retries of #257 buy:** at the usual rate (1 in 51) three attempts make a
  failed smoke negligible. In a burst (9 in 51, about 1 in 6) one request still loses all
  three attempts about 1 time in 180, so a 17-request smoke fails roughly 1 time in 10 -
  more if the stalls cluster. Not changed here: running the public smokes without the
  proxy would remove the stall, but whether the workstation may bypass it is the human's
  call.

## Local load-test stack (2026-11-09, #289)

The load-test programme (#281-#289) runs on the workstation; the human dropped the
dedicated VPS on 2026-11-09. How to use it: [loadtest.md](../loadtest.md).

- **Its own services, not an overlay of the dev ones.** `compose.loadtest.yaml` defines
  `lt-db`, `lt-app`, `lt-worker`, `lt-mcp` and `lt-tools` with their own database volume
  and no host ports. Overriding `db` and `app` instead would let `make test` or the
  pre-commit hook recreate the database container in the middle of a multi-hour seed.
  The services are in the `loadtest` profile, and the file is passed only by
  `scripts/task-compose --loadtest`.
- **Production-shaped where it matters for the numbers:** the runtime image, `APP_ENV=test`
  (which the stub usage generator of #281 requires), fsync on, the load-test budget from
  the shared facts, and one cpuset of 4 logical CPUs (`LOADTEST_SUT_CPUS`, default `4-7`)
  for the system under test.
- **The generator is not in the runtime image.** `loadtest/` runs in `lt-tools`, the dev
  image with the sources mounted read-only. The Dockerfile `loadtest` stage that was
  planned for the VPS is not built.
- **The operators' password** lives in `~/.config/bss/loadtest/operator-password`, outside
  every worktree, because the task secret file accepts only its four keys. It is passed to
  `lt-tools` as `LOADTEST_OPERATOR_PASSWORD`.
- **Users are created the real way:** `lt-admin` by `python -m app.users create`, the
  operators by the Users API as `lt-admin`, each followed by its first-sign-in password
  change. The generator never writes users with SQL.
- **The generator reads the database, never writes domain rows directly.** It uses SQL only
  to find what an interrupted day already created, and for its own `loadtest_config` and
  `loadtest_progress` tables.

## SQL plan sweep (2026-11-12, #327)

The suite-wide SQL plan sweep (#327, 2026-11-12) uses the local test server's
`pg_stat_statements` plus an opt-in psycopg attribution hook; normal tests do not
collect attribution. A dedicated collection run reserves one million statistics slots
for the cloned databases; normal local tests use 10k. Source lines and test IDs come
from successful application-issued statements, while DDL and fixture-only statements
remain in a separate export. Measurement runs offline on the restored `lt-db`, with
real account profiles, catalog-inferred bind types and explicit binding provenance.
Writes are rolled back and sequence values restored; constraints stay enabled.
Unsupported statements remain unbound and coverage below 90% fails. The runbook is
[loadtest.md](../loadtest.md#sql-plan-sweep-327).

## App processes and pools (2026-11-09, #288)

- **Processes.** `WEB_CONCURRENCY`, uvicorn's own variable, sets the number of app
  processes. The runtime image no longer passes `--workers 1`. `compose.prod.yaml` sets
  `WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}`, so the current VPS keeps one. The load-test
  overlay runs 4.
- **Pools.** Every process has its own pool of `DB_POOL_SIZE` connections (default 10; the
  load test uses 15). Rule: `WEB_CONCURRENCY x DB_POOL_SIZE + 1 + BILLING_PROCESSES + 3 <=
  max_connections`. The 1 is the worker's connection and the 3 cover pg_dump, a migration
  and a superuser session.
- **Limits from the environment** in `compose.prod.yaml`, today's values as defaults:
  `APP_CPUS` / `APP_MEM` (0.50 / 384m), `WORKER_CPUS` / `WORKER_MEM` (0.25 / 256m),
  `MCP_CPUS` / `MCP_MEM` (0.25 / 192m).
- **Nothing assumes one process.**
  - The sign-in throttle moved to the database (`sign_in_attempt`; keys, limits and
    messages unchanged).
  - Metrics run in Prometheus multiprocess mode, always (`docs/operations.md`, "Metrics").
  - What stays per process, accepted: the DB pools, the log-level poller (one cheap query
    every 15 s), the metrics database collector's 30 s cache, and the in-memory stub
    adapters (deterministic per call), and the request bound below.

## Request bound (2026-11-19, #341)

Found by the load test: at the 1x peak and production limits the app answered about 16
of 47 requests/s, held the rest, and grew from 109 to 386 MiB in 103 s until the kernel
killed it ([results](../loadtest-results.md#prod-shaped)). Nothing bounded the requests a
process holds: they queued for the threadpool and the DB pool, and memory grew with the
queue.

- **Requests, not connections.** `InFlightLimitMiddleware` (`app/overload.py`, pure ASGI)
  counts the requests one process is serving. Beyond `BSS_MAX_IN_FLIGHT` a request gets,
  at once and without touching the database, `503` with `Retry-After: 1` and the TMF error
  body (`code: "overloaded"`). Uvicorn's `--limit-concurrency` was rejected for the app:
  it counts open connections, and Caddy (vps-infra, shared) keeps idle keep-alive
  connections open, so it would refuse requests while the app is idle. The MCP keeps its
  `--limit-concurrency 32` (its own process and history, [mcp.md](mcp.md)).
- **Default `DB_POOL_SIZE`** (10 with the pool of 10), per process, `ge=1`. The issue
  planned `2 x DB_POOL_SIZE`; the load test changed it (2026-11-20, prod-shaped on one
  settled dump): with 20 the app survived but its memory settled at 330 MiB, 87 % of its
  384m; with 10 it settled at 228 MiB (59 %), answered more requests (54 % vs 51 % of
  those offered) and halved the read p95 (499 vs 1,038 ms). A request beyond the pool
  only waits for a connection while holding a thread and memory. `BSS_MAX_IN_FLIGHT`
  overrides it (`compose.prod.yaml` passes it through, empty = the default; the load-test
  overlay takes `LOADTEST_MAX_IN_FLIGHT`).
- **Exempt:** `/healthz` (a probe failing under load would take the container out, the
  outcome this avoids) and `/metrics` (the scrape is how the load is seen).
- **Order:** inside `RequestLogMiddleware` and `HttpMetricsMiddleware`, so a rejected
  request is logged and counted (status 503, route `unmatched`: no route has matched yet).
- **Metrics:** `bss_http_rejected_overload_total` (counter) and `bss_http_in_flight` (the
  admitted requests, a `livesum` gauge; `bss_http_requests_in_progress` also counts the
  rejected ones while they are answered). Rejections also count in `BssHigh5xxRate`.
- **Proof:** `make loadtest SCENARIO=prod-shaped` before and after, in
  [loadtest-results.md](../loadtest-results.md#prod-shaped).

## Supply-chain scanning (2026-11-08)

Issue #241. Known vulnerabilities in what we ship, with free tooling only: CodeQL, dependency
review and secret scanning are paid on a private repository.

- **A separate workflow**, `.github/workflows/security.yml`. It runs on pull requests that
  change the dependency files, the `Dockerfile`, the ignore files, the gate or the workflow
  itself. It also runs nightly at 01:45 UTC, after CI, and on `workflow_dispatch`, with
  `contents: read`. It is not a required check, because a required check with a path filter
  would hang on every other PR. On a PR it blocks through `make check-pr`. A red nightly run is
  the signal for a new vulnerability ([operations.md](../operations.md#security-scan)).
- **Three jobs, each fails on a finding.** `python`: pip-audit (pinned in
  `requirements-security.txt`, CI only) checks `constraints.txt` and `constraints-dev.txt`,
  and every finding blocks, since pip-audit reports no severity. `ui`: `npm audit --json` in
  the pinned Node image (lockfile only, no install) blocks at `high` for production packages
  and `critical` for dev-only ones. `image`: Trivy scans the `runtime` image for fixable HIGH
  and CRITICAL findings. Trivy runs from its official image pinned by digest, not from a
  third-party action, which keeps the supply-chain surface small. The image build reads CI's
  layer cache and writes its own cache scope, so it does not evict the cache of CI's dev image.
- **The gate**, `scripts/audit_gate.py`, is stdlib only and tested in `make runtime-check`.
  It reads the tool's JSON on stdin. Exit 0: nothing blocks. Exit 1: a finding or an
  ignore-file problem blocks. Exit 2: the input is JSON the gate doesn't understand (it never
  passes silently). npm audit's report has no dev-only flag, so the gate reads `dev` from
  `frontend/package-lock.json`. A finding counts as dev-only when every installed copy of the
  package has the flag. A copy missing from the lockfile counts as production (decided on the
  issue).
- **Accepting a finding is explicit and expires.** For pip and npm, an entry in
  `ops/security/audit-ignore.txt` has the form `<id> <review-by> <reason>`. The gate fails on
  an entry whose date has passed, on a date more than 60 days ahead, and on a malformed line.
  For the image, `.trivyignore` uses Trivy's own `exp:` date, with a reason comment above each
  entry.
- **Dependabot version updates cover the GitHub Actions only** (`.github/dependabot.yml`):
  one grouped PR a month that keeps the SHA pins. Python and UI pins change only
  deliberately ("Dependencies are pinned", above), so there are no routine
  pip, npm or docker version PRs. A fixable OS finding is handled by bumping the base image
  through its documented procedure.
- **Dependabot alerts and security updates are on** (repository settings, enabled by the
  human; checked 2026-11-08, #268). When an advisory hits a dependency GitHub knows,
  Dependabot raises an alert and opens a fix PR. GitHub's dependency graph reads
  `requirements*.txt`, `pyproject.toml` and `frontend/package*.json`, **not
  `constraints*.txt`**: a vulnerable transitive Python package (like `opentelemetry-api` and
  `certifi` in #263) gets no alert and no PR. The `Security` workflow's pip-audit, which
  reads the constraints files, is the check for those. Trivy covers the image.
- **A Dependabot security PR is a signal, never merged as it is**, like a red nightly
  `Security` run. It touches only the manifest, so a Python bump leaves `constraints*.txt`
  at the old version and the `-c` install cannot resolve. Handle it as a task: an issue that
  links the PR, the fix in a task branch (Python: the documented pin change with
  constraints regenerated from `pip freeze`; npm: Dependabot's `frontend/package*.json`
  change may be taken over as is, checked with `make web-install` and the UI checks), and
  the Dependabot PR closed with a link to the task PR.
- **The first run's findings** were not upgraded in #241. They were accepted with review-by
  2026-12-08, and #263 fixed them: Python pins bumped, pip removed from the runtime venv
  ("No pip in the runtime venv", above), base image digest bumped. Only
  the two zlib1g CVEs remain accepted, renewed, because no base image ships the fix yet.

## k6 load-test harness (2026-11-13, #281)

The local scenario runner uses the existing task load-test stack, separate SUT and k6
CPU sets, a digest-pinned Grafana k6 image (Grafana's GHCR copy after Docker Hub daemon
pulls failed), and per-run throwaway MCP and metrics tokens. Each ID pool shares
individual rows through a k6 `SharedArray`; sharing one enclosing dataset object caused
whole-dataset copies and an interrupted generator at 1x. API VUs reuse 20 sequentially
created operator sessions; UI VUs sign in once, staggered, and cookies persist across
iterations. A burst of password hashing for every API VU previously exhausted app memory.

Reports require all seven endpoint groups, no dropped iterations, and an actual k6
summary. Missing summaries cannot produce passing reports. These are harness fixes,
not permission to lower the approved load.

Four findings of the first runs (2026-11-12) and what was decided for each:

- **The feed.** One batch of 500 per wake-up is at most 8.3 records/s; the feed fell 47
  min behind 23/s. The worker's feed step now takes batches until the feed is empty or
  120 s have passed ([usage.md](usage.md#feed)). It is still the existing step: no new
  process, queue or setting.
- **The feed's rate in a scenario.** 1x is the peak load, so the peak scenarios (steady,
  stress, soak, prod-shaped) run the feed at the peak hour's 70 records/s and
  billing-night at the night's 8/s, whatever the time of day: `BSS_STUB_USAGE_HOUR` pins
  the generator to one hour of its daily shape. Without it a run at 14:00 would test 21/s
  and call that the peak.
- **MCP's limit.** bss-mcp allows 60 calls a minute per principal; 2/s through one
  service token is 120. The limit stays as in production: the runner spreads the mcp
  group over enough throwaway read-only tokens to keep each under 40 a minute (3 at 1x).
- **Billing-night's date.** The dataset's dates are frozen, so "today's run" is its first
  unbilled date: one catch-up of that one date is one daily run of 100k accounts. The
  worker leaves such a gap to the operator, so nothing else bills it first. The feed lag
  is reported but not judged there: the feed waits while the worker bills.

Results: [loadtest-results.md](../loadtest-results.md).
