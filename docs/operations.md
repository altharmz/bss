# Operations

How to develop, test, deploy and watch bss. This is a summary with links; the binding
rules are in [CLAUDE.md](../CLAUDE.md) (sections named in brackets below), which wins
where the two differ.

## Local development (WSL2)

Everything runs in containers: `docker compose` merges `compose.yaml` with
`compose.override.yaml` (local PostgreSQL `db`, the app with hot reload on
`127.0.0.1:8080`, read-only source mounts; Node services for the UI). **Use the make
targets; do not invent commands** (CLAUDE.md, "Commands").

| Target | What |
|---|---|
| `make up` / `make down` / `make logs` / `make ps` | start (and wait for healthy), stop, last 5 min of app logs, status |
| `make migrate` | `alembic upgrade head` against the local stack |
| `make seed` | demo data **through the public TMF API** with a FixedClock that walks 120 days forward, billing each day; refuses `APP_ENV=prod` and any database but the local one; a second run is a no-op (`tests/seed.py`, `tests/test_seed.py`) |
| `make worker-once` | one wake-up of the billing worker, then exit (there is no long-running local worker) |
| `make psql` | interactive psql in the local db (the db port is not published) |
| `make test` | `pytest -n auto` in the app container |
| `make lint` / `make fmt` | ruff check + format check / auto-format (the writable `tools` service) |
| `make smoke` | `scripts/smoke.sh http://localhost:8080`: `/healthz`, `/`, `/billing-status` |
| `make progress` | regenerate [PROGRESS.md](PROGRESS.md) from [progress/scope.yaml](progress/scope.yaml) and the code |
| `make docs` / `make docs-check` | regenerate [api/](api/README.md) and [db/](db/README.md) / fail if the committed ones differ |
| `make clean` | stop the stack **and drop the local database volume** |
| `make web-install` | the only step that downloads npm packages (`npm ci`, exactly the lockfile) |
| `make web-dev` / `web-build` / `web-lint` / `web-test` / `web-types` / `web-fmt` / `web-e2e` | the UI, always in the `web` / `web-offline` containers; offline targets fail with "run make web-install" if node_modules does not match the lockfile |
| `make hooks` | install the pre-commit and pre-push hooks (symlinks) |
| `make spec-verify-upstream` | re-fetch the spec blob SHAs from GitHub and compare with `spec/README.md` (network) |

The local PostgreSQL runs with `fsync`, `full_page_writes` and `synchronous_commit` off
for test speed - not crash-safe; recover with `make clean && make up` (CLAUDE.md,
"Conventions"). Node never runs on the host (CLAUDE.md, "Frontend conventions").

## Tests

| Suite | Command | What it covers |
|---|---|---|
| Backend | `make test` (`pytest -n auto`; 586 tests) | every test gets its own database cloned from a template migrated to head and dropped afterwards (`tests/conftest.py`); every API response is validated against `spec/` (`tests/apiclient.py`); hypothesis properties with 50 examples (`dev`) or 200 in `make verify` (`ci`, `HYPOTHESIS_PROFILE`) |
| Migrations | part of `make test` | upgrade on an empty database, downgrade to base and up again, as a NOSUPERUSER database owner like production, data-carrying upgrades of 0002 and 0004, and 0005's comments (`tests/test_migrations.py`, `tests/test_nonsuperuser_migration.py`) |
| Worker processes | part of `make test` | real `python -m app.worker` processes on a pinned clock, SIGTERM and SIGKILL (`tests/test_worker_signals.py`) |
| UI unit/component | `make web-test` | vitest, twice (UTC and `America/New_York`) |
| UI lint | `make web-lint` | tsc, eslint, prettier |
| UI end-to-end | `make web-e2e` | Playwright on the built UI against the seeded local stack, read-only |
| Generated docs | `make docs-check` | docs/api and docs/db match the code |

### In a cloud session

See [cloud.md](cloud.md). No Docker image can be built there (the build has no network)
and Node is blocked on the host, so only the backend runs: `bash scripts/cloud-setup.sh`
(PostgreSQL 16 on the host, a pinned Python 3.12 venv in `/opt/bss-venv`), then ruff and
`pytest -n auto -p no:cacheprovider` against the host database. The documentation
generators run the same way (`python -m tests.docs_collect > .docs/collected.json`, then
`python3 scripts/docs/generate.py`). **Cloud work is pushed to a branch and counts as
unverified until a local `make verify` passes.**

## Hooks

- **Pre-commit** (`scripts/pre-commit.sh`, installed by `make hooks`): refuses the commit
  unless the working tree is exactly what is committed, then `make lint` and `make test`
  (plus `make web-lint` and `make web-test` when `frontend/` or `spec/` is staged); keeps a
  full log of every run under `.git/pre-commit-logs/`. Skip deliberately with `git commit
  --no-verify` (CLAUDE.md, "Pre-commit hook").
- **Pre-push** (`scripts/pre-push.sh`): scans only the commits being pushed and refuses a
  bcrypt hash, an added `.env*` path (other than `.env.example`), a branch deletion or a
  non-fast-forward. Never bypassed (CLAUDE.md, "Pre-push hook").

## Deploy

`make deploy` runs `scripts/deploy.sh`, the **only** script allowed to change anything on
the VPS. `make verify` is the gate: `lint`, `test` (hypothesis `ci`), `docs-check`,
`web-lint`, `web-test`, `web-e2e`, then the deploy - it exits non-zero and prints remote
logs when anything fails.

What `deploy.sh` does, in order:

1. **Refuses a dirty tree** (`git status --porcelain`): the image tag is the commit hash.
2. **Refuses without the smoke credential** in the local `.env.smoke`, and if `.env.prod`
   carries a `SMOKE_BASIC_AUTH` line (it would be shipped). Builds the UI locally with the
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
8. `up` - waits until **both `app` and `worker` are healthy** (90 s).
9. Smoke on the VPS: `/healthz`, `/`, and `/billing-status` (200 must say `ok`; 503 one of
   `stale|failed|running|none`).
10. Rsyncs the built UI **into** `~/bss/web` (hashed assets, then `index.html`, then a
    `--delete` pass; the directory is never replaced, so Caddy's bind mount keeps it).
11. **Web smoke** through Caddy as the GET-only `smoke` user (`scripts/web-smoke.sh`):
    401 without auth, this build's `index.html`, a deep link, a hashed asset,
    `/internal/v1/context` as JSON with `operator` = `smoke`, a TMF list with
    `X-Total-Count`, `POST` -> 403, `/healthz` open.
12. Prunes old `bss-app` images on the VPS, keeping the newest few (nothing else is
    touched).

A deploy is **not fully reversible once a migration shipped**: rolling the image back does
not roll the schema back (`alembic downgrade` by hand). The deployed commit is not yet
recorded on the VPS (planned; CLAUDE.md, "Open checks"). Production config, limits and
the hard constraints of the VPS (VPN ports, no prune, nothing published on 0.0.0.0): see
CLAUDE.md, "Hard constraints". Never run `docker build` on the VPS.

## Backups

**Not part of this repository.** vps-infra dumps every database on the host, this one
included, by cron; bss has no backup or restore tooling (CLAUDE.md, "Shared
infrastructure"). Make sure a fresh dump exists before any migration that alters or
removes data. A restore drill from a cron-made dump is an open check (CLAUDE.md, "Open
checks"). There is **no `/backup-status` endpoint** in bss; backup monitoring, if any,
belongs to vps-infra (see [doc-findings.md](doc-findings.md)).

## Monitoring

| Signal | Where | Meaning |
|---|---|---|
| `GET /healthz` | app, unauthenticated | 200 when the app can reach the database, else 503 |
| `GET /billing-status` | app, unauthenticated | billing freshness: 200 `ok`/`running`, 503 `stale`/`failed`/`running`(overdue)/`none` ([billing-worker.md](billing-worker.md#billing-status)) |
| app healthcheck | `Dockerfile` | bash `/dev/tcp` probe of `/healthz` every 60 s |
| worker healthcheck | `compose.prod.yaml` | `/tmp/worker-alive` younger than 180 s |
| logs | `make logs` (local), json-file with rotation on the VPS | 500s are logged with their traceback |

Docker healthchecks are **informational only**: nothing restarts or replaces an
`unhealthy` container (`restart: unless-stopped` reacts to exits). Real alerting is the
external monitor polling `/healthz` and `/billing-status` (configured outside this
repository).

## The agent harness

Claude Code sessions on this repository run with guard rails against **agent mistakes,
not a hostile agent** (CLAUDE.md, "Agent guardrails: what they are for"):

- `.claude/settings.json`: `allow` (make, docker compose, read/edit of `app/`, `tests/`,
  `frontend/`, plain pushes), `ask` (edits of `.claude/**`, compose files, `Makefile`,
  `scripts/**`, `docs/progress/scope.yaml`), `deny` (reading or editing `.env*`, docker
  prune and volume removal, `sudo`, Node tools on the host, edits under `design-ref/`).
- `.claude/hooks/guard.sh` / `guard.py`: a quote-aware pattern matcher run before every
  shell command - blocks reading secrets, destructive docker cleanups, anything touching
  the VPN or its ports, shell writes to harness files, host Node, and force/delete/tag
  pushes. Fails closed on commands it cannot tokenise. Regression cases:
  `.claude/hooks/guard-cases.txt`, run by `.claude/hooks/test-guard.sh`.
- `.claude/hooks/format.sh`: formats edited Python (ruff) and UI files (prettier) in
  containers.
- **Approval rules**: commits need the human's OK; pushing an approved commit is allowed;
  force, delete, mirror, prune and tag pushes are forbidden; cloud sessions push only to
  `cloud/*` branches; changes to the Dockerfile, compose files, deploy script or data
  model start with a plan; a blocked command is reported, never worked around (CLAUDE.md,
  "Hard constraints", "Working style").
