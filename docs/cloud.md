# Working on bss in a cloud session (claude.ai/code)

Probed on 2026-10-09 at commit `292fee5` (branch `cloud/env-probe`). The coverage report
from the same probe is `docs/cloud-coverage-report.md`.

## The rule

**Cloud work happens on branches, is pushed, and counts as unverified until a local
`make check` passes before merge.**

- Never on `master`. One branch per piece of work (`cloud/<topic>` or the session's
  branch); push it - the container is ephemeral and is reclaimed when the session ends.
- What the cloud can prove is listed below; it is a subset of the gate. In particular no
  Docker image is built here, the e2e smoke does not run, and nothing can reach the VPS.
  The human checks out the branch on the workstation and runs `make check` (lint, test,
  docs-check, web-lint, web-test, web-e2e) before merging, and `make verify` (= check +
  deploy) when it ships. Until then, a green cloud run is a hint, not a result.
- The rules of CLAUDE.md still hold here (no `.env*`, edit files only with the Edit/Write
  tools, guard blocks are reported, not worked around).
- **Cloud mode: `BSS_CLOUD=1`**, set in the cloud environment's configuration (never on
  the workstation). The guard then (`.claude/hooks/guard.sh`, `guard.py`):
  - allows `npm`, `npx` and `node` on the host - here the sandbox is itself the
    container; `pnpm`, `yarn`, `corepack` stay blocked. The "Node only in containers"
    rule is unchanged on the workstation;
  - blocks every command that mentions `vps-test` or the VPS's public domain, quotes
    included (even a `grep` for it - read those files with the Read tool);
  - lets a push through only with an explicit destination under `cloud/`: `git push -u
    origin cloud/<topic>`. A bare `git push`, `HEAD`, `master`, `main` or any other
    branch is blocked. Force, delete, mirror and tag pushes are blocked everywhere.
- Never try to reach `vps-test` or its public domain from a cloud session.

## The environment

| What | Found |
|---|---|
| OS | Ubuntu 24.04.5 LTS, kernel 6.18, 4 cores, 16 GB RAM, no swap; runs as root |
| Python | `python3` = 3.13.16; 3.11 and **3.12** also installed (the image uses 3.12) |
| Node | 22.22.0 in `/opt/node22` (also node20/21, nvm); allowed on the host in cloud mode (`BSS_CLOUD=1`) |
| Docker | client 29.8.2 and `dockerd` installed, daemon **not running**; started by hand (`dockerd &`) it works: pulls and runs containers |
| PostgreSQL | **16.15 preinstalled** (cluster `16/main`, stopped); `pg_ctlcluster 16 main start` works |
| ruff | 0.16.8 on the PATH (`~/.local/bin`) - not the pinned 0.16.10; use the venv's |
| ssh / rsync / scp | **not installed**; raw TCP to port 22 (github.com) times out |
| Outbound network | HTTPS only, through an egress proxy with an allowlist. Reachable: PyPI, npm registry, GitHub (git over HTTPS, API), Ubuntu archive, Docker Hub (with intermittent `429 Too Many Requests`). Refused by the proxy (403): `apt.postgresql.org`, `example.com`, `www.google.com`, and the VPS's public domain |
| Docker containers' network | **no egress**: a container on a bridge network gets a TLS reset from npm/PyPI, and `apt-get` in a build cannot fetch `deb.debian.org` (plain HTTP; the proxy is CONNECT-only and listens on the host's loopback) |
| Disk | ~31 GB free |

During the probe the VPS's public domain (the one in CLAUDE.md, "Reverse proxy") was
included by mistake in a list of hosts probed with a single HTTPS `curl`; the egress
proxy refused the CONNECT (403), so nothing reached the VPS. Do not repeat it.

## What runs here and what does not

| Check | Here | How |
|---|---|---|
| `make lint` | **no** (image build fails: apt has no network) | equivalent: `ruff check app tests alembic && ruff format --check app tests alembic` with the pinned ruff - exactly the paths the `app` container mounts. Passed. (`ruff check .` on the host also lints `.claude/` and `scripts/`, which the container never sees; those report E501/format findings that `make lint` does not.) |
| `make test` | **no** (same build) | equivalent: `pytest -n auto` in the pinned venv against host PostgreSQL 16 - **585 passed, ~45 s**, four runs. Differences from the container: PG 16.15 (Ubuntu) instead of `postgres:16-alpine`, the venv instead of the image, the host's tzdata |
| `make progress` | partly | `python -m tests.progress_collect` runs in the venv; the UI marker collector needs Node (blocked); `scripts/progress/generate.py` runs (standard library). Output written to a scratch directory matched the committed files exactly |
| `make web-lint`, `make web-test` | **in cloud mode, on the host** | the container route needs `make web-install`, whose `npm ci` cannot reach the registry from inside a container ("Exit handler never called!"). With `BSS_CLOUD=1`, `cloud-setup.sh` runs the same `npm ci` (`frontend/scripts/install-deps.sh`) on the host with Node 22; then `cd frontend && sh scripts/check-deps.sh && npm run lint` (= `make web-lint`) and `... && npm test` (= `make web-test`, both TZ runs), with `SPEC_DIR=../spec` (the container mounts `spec/` at `/spec`). Not yet run in a cloud session |
| `make web-build` | not needed | `npm run build` would work the same way; nothing here consumes `frontend/dist` |
| `make web-e2e`, `make seed` against a stack, `make up`, `make smoke` | **no** | need the built `app` image |
| `make deploy`, `make verify`, `make ssh`, `make tunnel`, `make remote` | **never** | no ssh, the VPS is out of bounds |
| `.claude/hooks/test-guard.sh` | yes | the cases write absolute paths as `$CLAUDE_PROJECT_DIR` (the probe's one failure was a hard-coded workstation path, fixed); `@cloud` cases run with `BSS_CLOUD=1`, all others with it removed, so the suite means the same here and on the workstation. Proven on the workstation with `CLAUDE_PROJECT_DIR=/home/user/bss` and with `BSS_CLOUD=1` exported |

## The harness in a cloud session

- **Hooks run.** `.claude/settings.json` is loaded; `guard.sh` uses `$CLAUDE_PROJECT_DIR`
  (here `/home/user/bss`), not a hard-coded `~/projects` path, and it is active: it
  blocked a host `node --version` during the probe (before cloud mode existed).
  `npm`/`npx`/`node` are no longer in settings.json's deny list - a deny rule cannot
  depend on `BSS_CLOUD` - so on the workstation only the guard blocks them.
- **The format hook** (`.claude/hooks/format.sh`, PostToolUse on Edit/Write) runs ruff
  and prettier through `docker compose run tools` / `web-offline`; neither image can be
  built or populated here, so on a `.py` or `frontend/` edit it reports "format hook
  FAILED to run". Expected; run the venv's `ruff format` on the file instead.
- **The pre-commit hook is not installed** in a fresh clone (`.git/hooks/` holds only
  samples) and **would not pass here**: run once by hand, `scripts/pre-commit.sh` failed
  at `make lint` (image build, apt exit 100). Commit with `git commit --no-verify` and say
  so in the commit message; the local `make verify` is the gate.
- **`git push` works** (since `1c9f98e`): settings.json allows `git push`, `git push
  origin:*` and `git push -u origin:*`, so a push never stalls on a prompt. In cloud mode
  the guard lets it through only to a `cloud/*` branch named on the command line: `git
  push -u origin cloud/<topic>` (see "The rule"). The container is ephemeral - push the
  branch before the session ends. `cloud/docs` was pushed this way. (In the probe, before
  `1c9f98e`, the push was blocked and the files went up through the GitHub connector,
  which dropped the executable bit - hence `bash scripts/cloud-setup.sh`.)

## Setup

`scripts/cloud-setup.sh` (root, no secrets, idempotent, ~20 s):

- starts the preinstalled PostgreSQL 16 cluster, creates a superuser `app` / `throwaway`
  and database `app`, and sets `fsync`, `full_page_writes`, `synchronous_commit` off and
  `max_connections` 200 (as the local `db` service; throwaway cluster);
- creates `/opt/bss-venv` (outside the repo) with Python 3.12, `pip==26.2.1`,
  `requirements*.txt` with `constraints*.txt` (`pip --timeout 30`: a stalled download
  fails instead of hanging), and fails unless `pip freeze` equals `constraints-dev.txt`;
- in cloud mode (`BSS_CLOUD=1`) installs the UI's dependencies on the host: Node 22 from
  `/opt/node22`, `npm ci` in `frontend/` through `frontend/scripts/install-deps.sh` (the
  same step as `make web-install`, lockfile hash included), skipped when `check-deps.sh`
  already passes.

Then, from the repo root:

```
bash scripts/cloud-setup.sh
export PATH=/opt/bss-venv/bin:$PATH DATABASE_URL=postgresql://app:throwaway@localhost:5432/app APP_ENV=dev
ruff check app tests alembic && ruff format --check app tests alembic   # = make lint
pytest -n auto -p no:cacheprovider                                       # = make test

# UI, cloud mode only
export PATH=/opt/node22/bin:$PATH SPEC_DIR=../spec   # the container mounts spec/ at /spec
(cd frontend && sh scripts/check-deps.sh && npm run lint)   # = make web-lint
(cd frontend && sh scripts/check-deps.sh && npm test)       # = make web-test
```

`-p no:cacheprovider` keeps pytest from writing `.pytest_cache` into the checkout.
Proven: from a stopped cluster and no venv, the script and both commands passed (585
passed).

Not set up, on purpose: Docker-based targets (the build has no network; `make web-e2e`
needs the `app` image), and anything that needs `.env`, `.env.prod` or `.env.smoke`. A SessionStart hook could
run `cloud-setup.sh` automatically; that is a `.claude/` change and needs the human's OK.
