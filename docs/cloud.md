# Working on bss in a cloud session (claude.ai/code)

Probed on 2026-10-09 at commit `292fee5` (branch `cloud/env-probe`). The coverage report
from the same probe is `docs/cloud-coverage-report.md`.

## The rule

**Cloud work happens on branches, is pushed, and counts as unverified until a local
`make check` passes before merge.**

- Never on `master`. One branch per piece of work, `cloud/<name>`; push it - the
  container is ephemeral and is reclaimed when the session ends. **A cloud push must name
  the branch: `git push -u origin cloud/<name>`** (a bare `git push` is blocked, see
  below).
- **Cloud sessions set themselves up**: the SessionStart hook runs
  `scripts/cloud-setup.sh` on startup and resume (see "Setup"); its summary, or a FAILED
  line with the log's tail, is the first thing in the session's context.
- What the cloud can prove is listed below; it is a subset of the gate. In particular no
  Docker image is built here, the e2e smoke does not run, and nothing can reach the VPS.
  The human checks out the branch on the workstation and runs `make check` (lint, test,
  docs-check, web-lint, web-test, web-e2e) before merging, and `make verify` (= check +
  deploy) when it ships. Until then, a green cloud run is a hint, not a result.
- The rules of CLAUDE.md still hold here (no `.env*`, edit files only with the Edit/Write
  tools, guard blocks are reported, not worked around).
- **Cloud mode: `CLAUDE_CODE_REMOTE=true`**, which Claude Code itself sets in every cloud
  session and never on the workstation - nothing to configure. Only the exact value
  `true` counts. The guard then (`.claude/hooks/guard.sh`, `guard.py`):
  - allows `npm`, `npx` and `node` on the host - here the sandbox is itself the
    container; `pnpm`, `yarn`, `corepack` stay blocked. The "Node only in containers"
    rule is unchanged on the workstation;
  - blocks every command that mentions `vps-test` or the VPS's public domain, quotes
    included (even a `grep` for it - read those files with the Read tool);
  - lets a push through only with an explicit destination under `cloud/`: `git push -u
    origin cloud/<name>`. A bare `git push`, `HEAD`, `master`, `main` or any other
    branch is blocked - the guard cannot see which branch a bare push would update.
    Force, delete, mirror and tag pushes are blocked everywhere.
- Never try to reach `vps-test` or its public domain from a cloud session.

## The environment

| What | Found |
|---|---|
| OS | Ubuntu 24.04.5 LTS, kernel 6.18, 4 cores, 16 GB RAM, no swap; runs as root |
| Python | `python3` = 3.13.16; 3.11 and **3.12** also installed (the image uses 3.12) |
| Node | 22.22.0 in `/opt/node22` (also node20/21, nvm); allowed on the host in cloud mode |
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
| `make web-lint`, `make web-test` | **yes, on the host** | the container route needs `make web-install`, whose `npm ci` cannot reach the registry from inside a container ("Exit handler never called!"). `cloud-setup.sh` runs the same `npm ci` (`frontend/scripts/install-deps.sh`) on the host with Node 22; then `cd frontend && sh scripts/check-deps.sh && npm run lint` (= `make web-lint`) and `... && npm test` (= `make web-test`, both TZ runs), with `SPEC_DIR=../spec` (the container mounts `spec/` at `/spec`). Rehearsed in an Ubuntu 24.04 container (see "Setup"); not yet run in a real cloud session |
| `make web-build` | not needed | `npm run build` would work the same way; nothing here consumes `frontend/dist` |
| `make web-e2e`, `make seed` against a stack, `make up`, `make smoke` | **no** | need the built `app` image |
| `make deploy`, `make verify`, `make ssh`, `make tunnel`, `make remote` | **never** | no ssh, the VPS is out of bounds |
| `.claude/hooks/test-guard.sh` | yes | the cases write absolute paths as `$CLAUDE_PROJECT_DIR` (the probe's one failure was a hard-coded workstation path, fixed); `@cloud` cases run with `CLAUDE_CODE_REMOTE=true`, `@remote-false` ones with `false`, all others with it removed, so the suite means the same here and on the workstation. Proven on the workstation with `CLAUDE_PROJECT_DIR=/home/user/bss` and with `CLAUDE_CODE_REMOTE=true` exported |

## The harness in a cloud session

- **Hooks run.** `.claude/settings.json` is loaded; `guard.sh` uses `$CLAUDE_PROJECT_DIR`
  (here `/home/user/bss`), not a hard-coded `~/projects` path, and it is active: it
  blocked a host `node --version` during the probe (before cloud mode existed).
  `npm`/`npx`/`node` are no longer in settings.json's deny list - a deny rule cannot
  depend on `CLAUDE_CODE_REMOTE` - so on the workstation only the guard blocks them.
- **SessionStart hook** (`startup|resume`): `bash "$CLAUDE_PROJECT_DIR/scripts/cloud-setup.sh"`,
  timeout 600 s. On the workstation it exits 0 at once (`CLAUDE_CODE_REMOTE` is not
  `true`); `bash` because a connector-made commit may drop the executable bit.
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
  push -u origin cloud/<name>` (see "The rule"). The container is ephemeral - push the
  branch before the session ends. `cloud/docs` was pushed this way. (In the probe, before
  `1c9f98e`, the push was blocked and the files went up through the GitHub connector,
  which dropped the executable bit - hence `bash scripts/cloud-setup.sh`.)

## Setup

**Cloud sessions set themselves up.** The SessionStart hook (`.claude/settings.json`,
matcher `startup|resume`, timeout 600 s) runs `scripts/cloud-setup.sh` on every start and
resume. It exits 0 at once unless `CLAUDE_CODE_REMOTE` is `true` (so on the workstation
it does nothing), needs no secrets, and:

- starts the preinstalled PostgreSQL 16 cluster on every run (a resumed container may
  have stopped it), creates a superuser `app` / `throwaway` and database `app` if
  missing, and sets `fsync`, `full_page_writes`, `synchronous_commit` off and
  `max_connections` 200 once (as the local `db` service; throwaway cluster);
- installs `/opt/bss-venv` (outside the repo: Python 3.12, `pip==26.2.1`,
  `requirements*.txt` with `constraints*.txt`, `pip --timeout 30`) **only when** its `pip
  freeze` differs from `constraints-dev.txt`;
- runs `npm ci` in `frontend/` with Node 22 from `/opt/node22`, through
  `frontend/scripts/install-deps.sh` (the same step as `make web-install`, lockfile hash
  included), **only when** `check-deps.sh` fails;
- **always exits 0**: installer output goes to `/tmp/bss-cloud-setup.log`; the session's
  context gets either "cloud-setup: ready in N s" with the commands below, or
  "cloud-setup: FAILED" with the log's last 15 lines and the re-run command
  (`CLAUDE_CODE_REMOTE=true bash scripts/cloud-setup.sh`). A failed setup never blocks
  the session; the step that failed is simply missing.

Then, from the repo root (the hook prints the same):

```
export PATH=/opt/bss-venv/bin:/opt/node22/bin:$PATH DATABASE_URL=postgresql://app:throwaway@localhost:5432/app APP_ENV=dev SPEC_DIR=../spec
ruff check app tests alembic && ruff format --check app tests alembic   # = make lint
pytest -n auto -p no:cacheprovider                                       # = make test
(cd frontend && sh scripts/check-deps.sh && npm run lint)                # = make web-lint
(cd frontend && sh scripts/check-deps.sh && npm test)                    # = make web-test
```

`-p no:cacheprovider` keeps pytest from writing `.pytest_cache` into the checkout;
`SPEC_DIR=../spec` because the web container mounts `spec/` at `/spec`.

**Rehearsed 2026-10-09** on the workstation in a throwaway `ubuntu:24.04` container as
root (PostgreSQL 16 and Python 3.12 from the Ubuntu archive, Node 22.22.0 in
`/opt/node22`, the repo copied in without ignored files): not cloud -> exit 0, nothing
done; first start -> ready in 264 s; resume -> 0 s; resume with PostgreSQL stopped ->
started again, 2 s; every run exit 0. Then lint, pytest, `npm run lint` and `npm test`
(184 x2) passed. On the workstation, non-root with `CLAUDE_CODE_REMOTE=true`: "FAILED ...
run as root", exit 0. Not yet seen in a real cloud session (the egress proxy, the hook
firing on resume).

Not set up, on purpose: Docker-based targets (the build has no network; `make web-e2e`
needs the `app` image), and anything that needs `.env`, `.env.prod` or `.env.smoke`.
