# bss

This project was generated from a shared template (`vps-lab-template`). `make init
NAME=... APP_PORT=... HTTPS_PORT=... DOMAIN=...` filled in the project name, the local/VPS
app port, the HTTPS port, and the public domain everywhere they appear (compose files,
Makefile, scripts, this file, README.md), created `.env` and `.env.prod` from
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
make tunnel    # forward the remote app to localhost:18081
make remote CMD="..."  # run a one-off command against the deployed image on the VPS
make hooks     # install the local pre-commit hook (make lint + make test)
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
- On the VPS the app binds to `127.0.0.1:18081` only. Nothing is published to
  0.0.0.0. To reach it from the workstation use an SSH tunnel: `make tunnel`.
- On the VPS this project lives in `~/bss` (`REMOTE_DIR`, filled in by `make init`).
  Every app from this template gets its own directory; never point two projects at the
  same one — each deploy overwrites `compose*.yaml` and `.env` there.
- Remote commands go through `scripts/deploy.sh` only. Do not open ad-hoc `ssh vps-test '...'`
  sessions that mutate state.
- Never read, print or commit `.env` / `.env.prod`. Use `.env.example` as the reference.
- Never export `.env.prod` variables into a shell that also runs the local stack (e.g.
  `set -a && . ./.env.prod`) — exported variables take precedence over `.env`, so local
  containers silently pick up production values. For a one-off check against prod
  variables, scope them to a single command instead: `env $(grep -v '^#' .env.prod | xargs) <command>`.
- Do not `git push`. Commit locally; the human pushes.
- **Do not add `db` or `caddy` services back to the production configuration**
  (`compose.prod.yaml`), and do not re-add a Caddyfile or backup scripts to this repo.
  On the VPS they live in the separate vps-infra project — see "Shared infrastructure".
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
compose.yaml              base definition: only what both modes share (the app image)
compose.override.yaml     local dev only (own postgres on `lab`, bind mount, --reload, ports)
compose.prod.yaml         VPS only (shared network + alias, DATABASE_URL, limits, log rotation)
scripts/init.sh           one-time: fills in placeholders, creates .env/.env.prod
scripts/deploy.sh         build -> save -> ssh load -> migrate -> up -> healthcheck -> smoke
scripts/smoke.sh          HTTP smoke test against a base URL
scripts/pre-commit.sh     make lint + make test; installed via `make hooks`
tests/                    pytest suite (conftest.py spins up throwaway databases);
                          test_health.py and test_migrations.py check the infrastructure
                          this template provides and must keep passing on an otherwise
                          empty service
```

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
  ambiguous; always use `bss`.
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

## Frontend conventions

If this service grows a UI, static assets belong in `app/static/`, served by FastAPI via
`StaticFiles` — plain HTML/CSS/JS. No bundlers, no npm, no build step, no external CDN
dependencies. If a task seems to need a framework, say so and stop rather than adding a
Node toolchain to the image.
