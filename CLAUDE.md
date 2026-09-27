# __PROJECT__

This project was generated from a shared template (`vps-lab-template`). `make init
NAME=... APP_PORT=... HTTPS_PORT=... DOMAIN=...` filled in the project name, the local/VPS
app port, the HTTPS port, and the public domain everywhere they appear (compose files,
Makefile, scripts, this file, `Caddyfile`), created `.env` and `.env.prod` from
`.env.example` with a generated database password, and then removed itself from the
Makefile so it can't run twice. If an `init:` target still exists in the Makefile, or a
double-underscore token still shows up in a `grep -r` over the repo, `make init` was never
run — do that before anything else, not a manual find-and-replace, since it also creates
the `.env` files and only it knows to remove itself afterward.

FastAPI + PostgreSQL service. Developed locally in WSL2, verified on a remote VPS.

## Environments

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM, 30 GB disk) | verification only — pre-built image, no build |

**Images are built locally and shipped to the VPS. Never run `docker build` on the VPS** —
it has one core and a production VPN server running next to us.

## Commands — use these, do not invent your own

```
make up        # start local stack (app + postgres) with hot reload
make down      # stop local stack
make logs      # last 5 min of app logs
make test      # pytest inside the container
make lint      # ruff check + format check
make smoke     # smoke-test the local instance
make deploy    # build locally, ship image to VPS, restart there
make verify    # deploy + wait for healthy + remote smoke test
make ssh       # open a shell on the VPS
make migrate   # apply Alembic migrations against the local stack
make fmt       # auto-format (ruff format + check --fix)
make clean     # stop the local stack AND drop the local database volume
make tunnel    # forward the remote app to localhost:__APP_PORT__
make restore FILE=...  # restore a dump onto the VPS (human confirmation required)
make remote CMD="..."  # run a one-off command against the deployed image on the VPS
make hooks     # install the local pre-commit hook (make lint + make test)
make backup    # pull a database dump from the VPS to backups/ (local, manual)
make backup-list  # list the automated dumps sitting on the VPS
make cron-install # install/refresh the daily 02:30 backup cron job on the VPS
```

`make verify` is the single source of truth for "does it actually work on the server".
It exits non-zero and prints remote logs when anything fails.

## Pre-commit hook

`scripts/pre-commit.sh` runs `make lint` then `make test` (~16s) and blocks the commit on
failure.

- Not installed by default — `.git/hooks/` is never tracked by git, so every clone (this
  one included) needs one `make hooks` run. It installs a symlink, not a copy, so edits to
  `scripts/pre-commit.sh` take effect immediately without reinstalling.
- Runs non-interactively: both `make` calls are given `</dev/null` so the hook never
  blocks waiting on stdin, whether it fires from a terminal, an editor's git integration,
  or any other non-interactive caller.
- To skip it deliberately (e.g. a WIP commit): `git commit --no-verify`. The hook's own
  failure output reminds you of this.

## Hard constraints

- The VPS also runs a **SoftEther VPN server**, which is its primary purpose.
  Ports **443, 992, 1194, 500, 4500, 5555** are off limits. Do not touch, inspect
  or reconfigure anything VPN-related.
- Never run `docker system prune`, `docker volume rm`, `docker network prune`,
  or stop/remove containers you did not start. Other people's containers may be there.
- On the VPS the app binds to `127.0.0.1:__APP_PORT__` only. Nothing is published to
  0.0.0.0. To reach it from the workstation use an SSH tunnel: `make tunnel`.
- Remote commands go through `scripts/deploy.sh` only. Do not open ad-hoc `ssh vps-test '...'`
  sessions that mutate state.
- Never read, print or commit `.env` / `.env.prod`. Use `.env.example` as the reference.
- Never export `.env.prod` variables into a shell that also runs the local stack (e.g.
  `set -a && . ./.env.prod`) — exported variables take precedence over `.env`, so local
  containers silently pick up production values. For a one-off check against prod
  variables, scope them to a single command instead: `env $(grep -v '^#' .env.prod | xargs) <command>`.
- Do not `git push`. Commit locally; the human pushes.
- `make deploy` is no longer fully reversible once a migration has shipped. Rolling the
  image back to a previous tag restores the old code, but not the database schema — if a
  migration already ran, reverting the image leaves the schema ahead of the code. Rolling
  back the schema requires running `alembic downgrade <revision>` by hand against the VPS
  database; there is no automated rollback path.

## Layout

```
app/main.py               FastAPI app: /healthz and a JSON status root
app/config.py              settings from environment variables (pydantic-settings)
app/db.py                  psycopg 3 connection + the liveness probe /healthz calls
alembic/                  migrations; alembic.ini reads DATABASE_URL from the env
Caddyfile                 reverse proxy config, used only when WITH_CADDY=true (see below)
compose.yaml              base definition (app + db)
compose.override.yaml     local dev only (bind mount, --reload, exposed ports)
compose.prod.yaml         VPS only (resource limits, restart policy, log rotation)
scripts/init.sh           one-time: fills in placeholders, creates .env/.env.prod
scripts/deploy.sh         build -> save -> ssh load -> up -> healthcheck -> smoke
scripts/smoke.sh          HTTP smoke test against a base URL
scripts/pre-commit.sh     make lint + make test; installed via `make hooks`
scripts/cron-backup.sh    lives on the VPS (shipped by deploy.sh); daily dump + rotation
scripts/cron-install.sh   installs/refreshes the crontab entry that runs cron-backup.sh
scripts/backup-list.sh    lists the dumps cron-backup.sh has left on the VPS
tests/                    pytest suite (conftest.py spins up throwaway databases);
                          test_health.py and test_migrations.py check the infrastructure
                          this template provides and must keep passing on an otherwise
                          empty service
```

`docker compose` picks up `compose.override.yaml` automatically, so plain
`docker compose up` is always the *local dev* stack. Production composition is explicit:
`-f compose.yaml -f compose.prod.yaml`.

## Conventions

- App listens on **8080** inside the container. Do not hardcode host ports in code.
- Config comes from environment variables only (see `app/config.py`). No config files.
- `/healthz` must stay cheap and must verify the DB connection — Docker's HEALTHCHECK
  and the deploy script both depend on it.
- Python 3.12, `ruff` for lint and format, `pytest` for tests.
- Dependencies: add to `requirements.txt` (runtime) or `requirements-dev.txt` (tooling),
  then rebuild. Do not `pip install` inside a running container.
- Before every commit: `make lint && make test`. Small, focused commits.

## Working style

- For anything that changes the Dockerfile, compose files, deploy script or the data
  model: propose a plan first, then implement.
- After changing runtime behaviour, run `make test`; before saying "it works on the
  server", run `make verify`.
- When a container misbehaves, read `make logs` before changing code.

## Reverse proxy

A Caddy container can front the app on the VPS: TLS + basic auth on `__HTTPS_PORT__`,
port 80 for ACME only. Public URL: `https://__DOMAIN__:__HTTPS_PORT__`

Whether *this* project's compose stack runs that Caddy container is controlled by
`WITH_CADDY` in `.env.prod`, via a compose profile — not by adding or removing the
`caddy` service:

- **`WITH_CADDY=true`** — this is the first service set up on the VPS, so it owns the
  shared Caddy. `compose.prod.yaml`'s `caddy` service (`profiles: ["caddy"]`) is started
  by passing `--profile caddy`, which `scripts/deploy.sh` does automatically when it reads
  `WITH_CADDY=true`. `Caddyfile` (checked into this repo, with placeholders filled in by
  `make init`) is the config that container runs.
- **`WITH_CADDY=false`** — another service on this VPS already runs Caddy. This stack
  never starts a `caddy` container at all; instead, this project's reverse-proxy block is
  added by hand to that already-running Caddy's Caddyfile on the server. See README.md for
  the block to copy and where it goes.

Do not "fix" a missing local Caddy container by flipping `WITH_CADDY` to true or by adding
a second, unconditional `caddy` service — on a VPS that already has one, that starts a
second Caddy fighting the first for ports 80/`__HTTPS_PORT__`. The profile is the whole
point: it lets both scenarios share one compose file.

- The bcrypt hash in `Caddyfile` is inlined on purpose: `basic_auth` does NOT expand
  `{env.*}` placeholders in the password field — the hash is parsed before placeholder
  substitution. Do not "fix" this by moving it back to an environment variable.
- `/healthz` is intentionally reachable without the basic auth password — carved out via
  its own `handle /healthz` block ahead of the catch-all `handle` that carries
  `basic_auth`, since `handle` blocks are mutually exclusive and the first match wins.
  External monitoring on a separate machine polls it directly. It returns nothing beyond
  service status and DB reachability, so exposing it unauthenticated doesn't leak
  anything — do not put other routes in that block.
- Do not touch ports 80 or `__HTTPS_PORT__` in `compose.prod.yaml` without saying why.
- Bind-mounted configs (the Caddyfile) are not picked up by a plain `up -d` — compose
  only diffs container config (image, env, volume list), never the *contents* of a
  mounted file, so an edited Caddyfile keeps running the old config in memory until the
  container is recreated. `scripts/deploy.sh` handles this itself (when `WITH_CADDY=true`):
  it checksums the Caddyfile before vs. after syncing and only when it actually changed
  runs a scoped `up -d --force-recreate --no-deps caddy`. Don't "fix" this class of problem
  elsewhere with an unconditional `--force-recreate` — it bounces the proxy on every deploy.
- The VPS reboots nightly at 03:00 (root cron). Everything must come back on its own —
  never rely on manually started containers.

## Frontend conventions

If this service grows a UI, static assets belong in `app/static/`, served by FastAPI via
`StaticFiles` — plain HTML/CSS/JS. No bundlers, no npm, no build step, no external CDN
dependencies. If a task seems to need a framework, say so and stop rather than adding a
Node toolchain to the image.

## Backups

Two independent, non-overlapping paths — a server backup does **not** protect against
losing the VPS itself; only a copy pulled to the workstation does.

- **Automatic, server-side**: `cron-backup.sh` runs on the VPS itself (shipped there by
  `scripts/deploy.sh`, installed into the `deploy` user's crontab by `make cron-install`,
  idempotent to re-run). Fires daily at 02:30 — 30 minutes before the nightly 03:00
  reboot — dumping into `~/lab/backups/` on the VPS and keeping the 7 most recent dumps.
  Inspect what's there with `make backup-list`. This is a rolling safety net against a bad
  migration or accidental data loss; it lives on the same disk as the database it's
  backing up.
- **Manual, pulled locally**: `make backup` dumps the remote database and pulls it into
  `backups/` on the workstation (gitignored — dumps never go into the repository). This is
  the one that survives losing the VPS entirely. Take one before any migration that alters
  or removes existing data.
- `make restore FILE=...` is never run automatically or by an agent. It overwrites the
  live database and requires a human to type `yes` after seeing the current row counts.
