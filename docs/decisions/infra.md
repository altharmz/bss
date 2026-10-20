# Infrastructure: image, layout, conventions, VPS, deploy

Index: [README.md](README.md).

## Template origin (`make init`)

This project was generated from a shared template (`vps-lab-template`). `make init
NAME=... APP_PORT=... HTTPS_PORT=... DOMAIN=...` filled in the project name, the local/VPS
app port, the HTTPS port, and the public domain everywhere they appear (compose files,
Makefile, scripts, this file, README.md), created `.env` and `.env.prod` from
`.env.example` with a generated database password, and then removed itself from the
Makefile so it can't run twice. If an `init:` target still exists in the Makefile, or a
double-underscore token still shows up in a `grep -r` over the repo, `make init` was never
run — do that before anything else, not a manual find-and-replace, since it also creates
the `.env` files and only it knows to remove itself afterward.

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
compose.prod.yaml         VPS only (shared network + alias, DATABASE_URL, limits, log rotation)
scripts/init.sh           one-time: fills in placeholders, creates .env/.env.prod
scripts/deploy.sh         build -> save -> ssh load -> migrate -> up -> healthcheck -> smoke
scripts/smoke.sh          HTTP smoke test against a base URL
scripts/pre-commit.sh     make lint + make test; installed via `make hooks`
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
  clone, dropped afterwards (a run leaves no `vpslab_%` database behind). Hypothesis
  examples per property come from a profile selected by `HYPOTHESIS_PROFILE`: `dev` (50)
  for `make test` and the pre-commit hook, `ci` (200) for `make verify` (a target-specific
  variable in the Makefile). Plain `pytest` without the variable uses `dev`.
  **Workers stay at `-n auto`** (one per core, 12 on the workstation): measured
  2026-10-20, 12 workers already keep the CPU ~91% busy, and `-n 24` saturated it,
  pushed the WSL VM into swap and made postgres fail the concurrent template migrations
  with `out of shared memory` (its lock table). The `api` fixture's TestClient runs
  inside `with`: one event-loop thread per test instead of one per request.
- **The local postgres runs with `fsync=off`, `full_page_writes=off`,
  `synchronous_commit=off`** (`command:` of `db` in `compose.override.yaml`): every test
  clones and drops a database, and with fsync on each drop waits for a server-wide
  checkpoint. Local only - production uses vps-infra's postgres and never reads
  `compose.override.yaml`, so it is unaffected. Not crash-safe: after a WSL crash (or a
  killed Docker VM) the local database may be corrupt; the recovery is `make clean`
  (drops the local volume) and `make up`.
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
- Before every commit: `make lint && make test`. Small, focused commits.

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
  without a pool's lifecycle issues (`--reload`, per-test databases). Decision deferred.

## Shared infrastructure (vps-infra)

On the VPS, Postgres and Caddy are **not ours**. The separate vps-infra project is
deployed once per host and runs one Postgres and one Caddy for every app there, owning
their config, data, TLS and backups. This repo only ships the app container.

- **Network**: the app joins the external network `shared`, created by vps-infra. It must
  exist before `make deploy`; `scripts/deploy.sh` checks for it and fails early with
  "deploy vps-infra first" otherwise. vps-infra is never needed locally — do not start it
  for development.
- **Alias**: on `shared` the app is reachable as `bss` (network alias in
  `compose.prod.yaml`). Every app there names its service `app`, so that name is
  ambiguous; always use `bss`. The MCP connector (`mcp`, [mcp.md](mcp.md)) is
  `bss-mcp:8080` there and calls the app as `bss:8080`.
- **Database**: `DATABASE_URL` on the VPS is `postgres:5432/bss`, role
  `bss`; `postgres` is the service name in vps-infra, and the database + role are
  created there with `make add-db NAME=bss`. The password is `POSTGRES_PASSWORD`
  from `.env.prod`, which must equal the role password `make add-db` used (`DB_PASSWORD_<NAME>` in
  vps-infra's `.env.prod`) — the value `make init`
  generated is temporary, and a mismatch makes migrations hang until the 120s timeout
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

The Caddy side is vps-infra's: **`docs/vps-infra-change-request.md`** has the complete
request - the read-only static mount of `~/bss/web` (created by vps-infra *before* Caddy is
restarted, else Docker creates it root-owned), the site block (validated with Caddy 2.11.4
in a local rehearsal), the GET-only `smoke` user, `/billing-status` unauthenticated, and
the order of deploys (backup, vps-infra, then `make verify`).

**First web deploy: done** - `9424b9b`, 2026-10-02, `make verify` end to end (migration
0003 ran, web smoke 8/8 through Caddy).

- **`deploy.sh`** builds the UI locally before any network step (`make web-build
  WEB_BUILD=<tag>` stamps `index.html`'s `bss-build` marker) and refuses to start without
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
No local deployed coverage file is written and no coverage percentages change.

Local validation: Bash syntax check passes. Isolated execution of the applied
record/read-back block passes an actual atomic write and read (including a custom
image tag) and fails on mismatched content, a failed write or a failed read. No network calls or
deployment were performed. The record contains exactly the full commit plus a newline,
and the temporary file is gone after the rename. Production confirmation awaits a
separately approved deployment.

`make lint`, `make test` (1,405 passed) and `make decisions-check` pass. Corrected
API notes were regenerated with `make docs`; a second generation left all 53 reference
pages identical. Scope is unchanged.

## To port to vps-lab-template

Two fixes made here and still missing in the template (moved from CLAUDE.md's "Open
questions").

- **Port the format fix to vps-lab-template** (fixed here, not there). In the template,
  `make fmt` and `.claude/hooks/format.sh` both fail silently:
  - `compose.override.yaml` mounts `app/` and `tests/` read-only on `app`, so
    `docker compose run app ruff format` cannot write ("Read-only file system");
  - the hook passes Claude Code's absolute host path into the container, where it does not
    exist, sends format output to /dev/null, prints `ruff check` to stdout (not shown for
    PostToolUse) and always exits 0.

  Fix as done here: a `tools` service in `compose.override.yaml` (same `dev` target and
  image, `profiles: [tools]`, only `./app`, `./tests`, `./alembic` mounted read-write and
  `./pyproject.toml` read-only under `/app` - not the whole project, so the container
  never sees `.env`, `.env.prod`, `.git` or `.claude` - venv stays in `/opt/venv`, no
  ports, no DB, `network_mode: none`); `make fmt` runs
  `docker compose run --rm tools sh -c 'ruff format . && ruff check --fix .'`, `make lint`
  stays on `app`. The hook: no host-ruff branch (the container pins the ruff version);
  acts only on `*.py` under `$CLAUDE_PROJECT_DIR`, passes the project-relative path; a
  `.py` outside the mounted directories is reported as FAILED, not skipped silently; one
  container start running `ruff format --force-exclude` then `ruff check --fix
  --force-exclude --output-format concise`; clean -> exit 0 silently, findings -> stderr +
  exit 2, any failure to run -> "format hook FAILED to run: <reason>" + exit 2; explicit
  `timeout` in `.claude/settings.json`.
- **Port the SSH hardening to vps-lab-template** (fixed here, not there). The template's
  `deploy.sh` opens a new SSH connection per step (~15 per deploy) with no timeouts. The
  VPS intermittently stalls *new* connections at the banner exchange
  (`kex_exchange_identification: read: Connection timed out`); an rsync hung for minutes
  and a `docker save | ssh` ship hung 17 min before failing. Fix as done here:
  - refuse a dirty tree first, before any network call: `git status --porcelain` must be
    empty - the tag is the commit hash, so a dirty build (image or compose files) would
    ship different content under a clean-looking tag;
  - one master per deploy: `SSH_OPTS=(-o ControlMaster=auto -o ControlPath=~/.ssh/cm-%C
    -o ControlPersist=60 -o ConnectTimeout=15 -o ServerAliveInterval=15
    -o ServerAliveCountMax=4 -o BatchMode=yes)`, opened explicitly with `ssh -MNf` before
    the build (or reused if `ssh -O check` already finds one), verified with `ssh -O check`;
  - the master is backgrounded, so it logs to a file: `-o ControlMaster=yes -o
    LogLevel=VERBOSE -E "$(mktemp)"`. One `trap finish EXIT` closes the master with
    `ssh -O exit` (only if this deploy opened it), prints the last 20 lines of that log
    when the exit code is non-zero, and removes the log;
  - every remote call rides the master: an `rssh` wrapper, `rsync -az --timeout=60 -e "ssh
    <same opts>"`, and `timeout N ssh "${SSH_OPTS[@]}"` where `timeout` needs a binary;
  - exactly one retry, 5 s apart, and only when opening the master exits 255; later steps
    are never retried;
  - skip the ship when the server already has the image. Not by `.Id`: both sides use the
    containerd image store, where `.Id` is the manifest digest and differs between two
    builds of identical content. Compare a fingerprint instead, computed by the same `sh`
    snippet locally and remotely: sha256 of `docker image inspect --format '{{json
    .RootFS.Layers}}{{json .Config}}'` (Layers alone miss a CMD/ENV/HEALTHCHECK-only
    change). A missing image yields an empty fingerprint and never matches;
  - Makefile `ssh`/`tunnel`/`remote` get the same timeouts and `BatchMode=yes` (no
    multiplexing there).

  Proven here: 1 TCP connection per deploy (sampled with `ss`), a dead endpoint (silent
  listener or blackhole IP) fails in ~35 s (15 + 5 + 15) with a clear message.
  Skip proven across hosts: two consecutive plain `make verify` runs of the same commit
  (`921603f`) against the VPS - the first shipped the image, the second printed "image
  already on server, skipping ship (fingerprint 8258832b6d9c)", so the Layers+Config
  fingerprint survives `docker save | docker load`. The master-open retry also fired once
  in real use, in the second run (exit 255, then success on the second attempt).
  Connection counts were not sampled in these two runs. A run where the retry fires shows
  2 connection attempts by design: the failed one plus the successful one.
