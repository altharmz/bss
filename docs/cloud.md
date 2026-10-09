# Working on bss in a cloud session (claude.ai/code)

Probed on 2026-10-09 at commit `292fee5` (branch `cloud/env-probe`). The coverage report
from the same probe is `docs/cloud-coverage-report.md`.

## The rule

**Cloud work happens on branches, is pushed, and counts as unverified until a local
`make verify` passes before merge.**

- Never on `master`. One branch per piece of work (`cloud/<topic>` or the session's
  branch); push it - the container is ephemeral and is reclaimed when the session ends.
- What the cloud can prove is listed below; it is a subset of the gate. In particular no
  Docker image is built here, the UI suites do not run, and nothing can reach the VPS.
  The human checks out the branch on the workstation and runs `make verify` (lint, test,
  web-lint, web-test, web-e2e, deploy + health + smoke) before merging. Until then, a
  green cloud run is a hint, not a result.
- The rules of CLAUDE.md still hold here (no `.env*`, edit files only with the Edit/Write
  tools, guard blocks are reported, not worked around). The "Node only in containers"
  rule is a **workstation** rule and is not changed: in the cloud the sandbox is itself
  the container, but the guard does not know that and still blocks host Node (below).
- Never try to reach `vps-test` or its public domain from a cloud session.

## The environment

| What | Found |
|---|---|
| OS | Ubuntu 24.04.5 LTS, kernel 6.18, 4 cores, 16 GB RAM, no swap; runs as root |
| Python | `python3` = 3.13.16; 3.11 and **3.12** also installed (the image uses 3.12) |
| Node | 22.22.0 in `/opt/node22` (also node20/21, nvm); host use blocked by the guard |
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
| `make web-lint`, `make web-test`, `make web-build` | **no** | host Node is blocked by `guard.sh` (and denied in settings.json); the container route needs `make web-install`, whose `npm ci` cannot reach the registry from inside a container ("Exit handler never called!") |
| `make web-e2e`, `make seed` against a stack, `make up`, `make smoke` | **no** | need the built `app` image |
| `make deploy`, `make verify`, `make ssh`, `make tunnel`, `make remote` | **never** | no ssh, the VPS is out of bounds |
| `.claude/hooks/test-guard.sh` | yes, with a caveat | 124/125 here; the failing case hard-codes the workstation path `/home/petrg/projects/bss/scripts/y` (`guard-cases.txt` line 119). With `CLAUDE_PROJECT_DIR=/home/petrg/projects/bss` all 125 pass |

## The harness in a cloud session

- **Hooks run.** `.claude/settings.json` is loaded; `guard.sh` uses `$CLAUDE_PROJECT_DIR`
  (here `/home/user/bss`), not a hard-coded `~/projects` path, and it is active: it
  blocked a host `node --version` during the probe. Only the regression case above
  assumes the workstation path.
- **The format hook** (`.claude/hooks/format.sh`, PostToolUse on Edit/Write) runs ruff
  and prettier through `docker compose run tools` / `web-offline`; neither image can be
  built or populated here, so on a `.py` or `frontend/` edit it reports "format hook
  FAILED to run". Expected; run the venv's `ruff format` on the file instead.
- **The pre-commit hook is not installed** in a fresh clone (`.git/hooks/` holds only
  samples) and **would not pass here**: run once by hand, `scripts/pre-commit.sh` failed
  at `make lint` (image build, apt exit 100). Commit with `git commit --no-verify` and say
  so in the commit message; the local `make verify` is the gate.
- **`git push` is denied** by settings.json and blocked by the guard (CLAUDE.md: "Do not
  `git push`"). A cloud session only keeps its work by pushing a branch, so a cloud task
  must say explicitly that pushing its branch is wanted; the push then happens only to
  that branch, never to `master`. In the probe the guard blocked `git push`; with the
  human's explicit OK the files went up through the GitHub connector instead (a commit
  made by the API, not the local one - which also drops the executable bit, so run the
  setup script as `bash scripts/cloud-setup.sh`).

## Setup

`scripts/cloud-setup.sh` (root, no secrets, idempotent, ~20 s):

- starts the preinstalled PostgreSQL 16 cluster, creates a superuser `app` / `throwaway`
  and database `app`, and sets `fsync`, `full_page_writes`, `synchronous_commit` off and
  `max_connections` 200 (as the local `db` service; throwaway cluster);
- creates `/opt/bss-venv` (outside the repo) with Python 3.12, `pip==26.2.1`,
  `requirements*.txt` with `constraints*.txt`, and fails unless `pip freeze` equals
  `constraints-dev.txt`.

Then, from the repo root:

```
bash scripts/cloud-setup.sh
export PATH=/opt/bss-venv/bin:$PATH DATABASE_URL=postgresql://app:throwaway@localhost:5432/app APP_ENV=dev
ruff check app tests alembic && ruff format --check app tests alembic   # = make lint
pytest -n auto -p no:cacheprovider                                       # = make test
```

`-p no:cacheprovider` keeps pytest from writing `.pytest_cache` into the checkout.
Proven: from a stopped cluster and no venv, the script and both commands passed (585
passed).

Not set up, on purpose: Docker-based targets (the build has no network), the UI
toolchain (blocked on the host by the guard - changing that is the human's decision),
and anything that needs `.env`, `.env.prod` or `.env.smoke`. A SessionStart hook could
run `cloud-setup.sh` automatically; that is a `.claude/` change and needs the human's OK.
