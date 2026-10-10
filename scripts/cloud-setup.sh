#!/usr/bin/env bash
# Cloud sessions only (claude.ai/code container, Ubuntu 24.04, root) - NOT the workstation.
# See docs/cloud.md. Runs as the SessionStart hook (.claude/settings.json, on startup and
# resume) and does nothing unless CLAUDE_CODE_REMOTE is "true" - Claude Code sets it in
# every cloud session and never locally. The workstation keeps the container-only rules of
# CLAUDE.md; here the sandbox itself is the container, and Docker builds cannot reach the
# Debian mirror or the npm registry, so the checks run on the host instead:
#
#   - the preinstalled PostgreSQL 16 cluster, started and tuned like the local `db`
#     service (fsync, full_page_writes, synchronous_commit off), with a superuser role
#     `app` / password `throwaway` - a throwaway cluster, never a secret;
#   - a Python 3.12 venv in /opt/bss-venv (outside the repo) installed exactly like the
#     dev image: pip==26.2.1, requirements*.txt with constraints*.txt; pip gives up on a
#     stalled download after 30 s (one hung for 10+ minutes without a timeout);
#   - the UI's dependencies, `npm ci` in frontend/ with Node 22 (the version of the web
#     image) through frontend/scripts/install-deps.sh - the same step as make web-install,
#     so check-deps.sh and the lockfile hash work as in the container.
#
# It runs on every resume, so each step is skipped when its result is already there:
# PostgreSQL is only (re)started, the venv is installed only when its pip freeze differs
# from constraints-dev.txt, npm ci only when check-deps.sh fails. Installer output goes to
# /tmp/bss-cloud-setup.log, not into the session's context.
#
# **Always exits 0**: a failed setup must not break the session - it prints
# "cloud-setup: FAILED ..." with the log's tail instead, and the session goes on without
# the step that failed. Needs no secrets and never reads .env*.
# Usage (by hand): CLAUDE_CODE_REMOTE=true bash scripts/cloud-setup.sh
set -uo pipefail

[[ "${CLAUDE_CODE_REMOTE:-}" == true ]] || exit 0

VENV=/opt/bss-venv
PG_VERSION=16
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/tmp/bss-cloud-setup.log

setup() {
  set -e
  [[ "$(id -u)" == 0 ]] || { echo "run as root (the cloud container's user)"; return 1; }
  command -v pg_ctlcluster >/dev/null || { echo "no postgresql-common (pg_ctlcluster)"; return 1; }
  command -v python3.12 >/dev/null || { echo "python3.12 not found"; return 1; }

  # --- PostgreSQL 16: started on every run (a resumed container may have stopped it) ---
  if ! pg_lsclusters -h | awk -v v="$PG_VERSION" '$1 == v && $2 == "main"' | grep -q .; then
    pg_createcluster "$PG_VERSION" main
  fi
  pg_ctlcluster "$PG_VERSION" main start >/dev/null 2>&1 || true # "already running" is fine
  pg_isready -q -h localhost || { echo "PostgreSQL $PG_VERSION did not start"; return 1; }
  psql_admin() { su postgres -c "psql -v ON_ERROR_STOP=1 -qtA $*"; }
  if [[ "$(psql_admin "-c \"SELECT 1 FROM pg_roles WHERE rolname = 'app'\"")" != 1 ]]; then
    psql_admin "-c \"CREATE ROLE app LOGIN SUPERUSER PASSWORD 'throwaway'\""
  fi
  if [[ "$(psql_admin "-c \"SELECT 1 FROM pg_database WHERE datname = 'app'\"")" != 1 ]]; then
    su postgres -c "createdb -O app app"
  fi
  if [[ "$(psql_admin "-c 'SHOW fsync'")" != off ]]; then
    # As compose.override.yaml's db: every test clones and drops a database. Not
    # crash-safe; this cluster is throwaway. max_connections: pytest -n auto on every core.
    psql_admin "-c 'ALTER SYSTEM SET fsync = off' -c 'ALTER SYSTEM SET full_page_writes = off' \
      -c 'ALTER SYSTEM SET synchronous_commit = off' -c 'ALTER SYSTEM SET max_connections = 200'"
    pg_ctlcluster "$PG_VERSION" main restart
  fi

  # --- Python 3.12 venv, pinned exactly like the dev image; only when it differs ---
  venv_current() {
    [[ -x "$VENV/bin/pip" ]] &&
      diff <(grep -Ev '^(#|$)' "$ROOT/constraints-dev.txt" | sort -f) \
        <("$VENV/bin/pip" freeze | sort -f) >/dev/null
  }
  if ! venv_current; then
    [[ -x "$VENV/bin/python" ]] || python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install -q --timeout 30 pip==26.2.1
    "$VENV/bin/pip" install -q --timeout 30 -r "$ROOT/requirements.txt" -c "$ROOT/constraints.txt"
    "$VENV/bin/pip" install -q --timeout 30 -r "$ROOT/requirements-dev.txt" -c "$ROOT/constraints-dev.txt"
    venv_current || { echo "the venv's pip freeze differs from constraints-dev.txt"; return 1; }
  fi

  # --- UI dependencies (the workstation installs them in the web container) ---
  [[ -x /opt/node22/bin/node ]] && export PATH="/opt/node22/bin:$PATH"
  node_major="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
  [[ "${node_major}" == 22 ]] || { echo "Node 22 not found (got '${node_major:-none}')"; return 1; }
  if ! (cd "$ROOT/frontend" && sh scripts/check-deps.sh); then
    (cd "$ROOT/frontend" && sh scripts/install-deps.sh)
    (cd "$ROOT/frontend" && sh scripts/check-deps.sh)
  fi
}

started=$SECONDS
# Not `if (setup)`: bash ignores set -e inside a command whose status is tested, so a
# failed step would not stop the setup.
(setup) >"$LOG" 2>&1
rc=$?
if ((rc == 0)); then
  cat <<EOF
cloud-setup: ready in $((SECONDS - started)) s (PostgreSQL 16 on localhost:5432, venv /opt/bss-venv = constraints-dev.txt, frontend/node_modules = package-lock.json). From the repo root (docs/cloud.md):
  export PATH=/opt/bss-venv/bin:/opt/node22/bin:\$PATH DATABASE_URL=postgresql://app:throwaway@localhost:5432/app APP_ENV=dev SPEC_DIR=../spec
  ruff check app tests alembic && ruff format --check app tests alembic   # = make lint
  pytest -n auto -p no:cacheprovider                                       # = make test
  (cd frontend && sh scripts/check-deps.sh && npm run lint)                # = make web-lint
  (cd frontend && sh scripts/check-deps.sh && npm test)                    # = make web-test
EOF
else
  echo "cloud-setup: FAILED after $((SECONDS - started)) s - the session goes on without it. Last lines of $LOG:"
  tail -n 15 "$LOG"
  echo "Re-run: CLAUDE_CODE_REMOTE=true bash scripts/cloud-setup.sh"
fi
exit 0
