#!/usr/bin/env bash
# Cloud sessions only (claude.ai/code container, Ubuntu 24.04, root) - NOT the workstation.
# See docs/cloud.md. The workstation keeps the container-only rules of CLAUDE.md; here the
# sandbox itself is the container, and Docker builds cannot reach the Debian mirror or the
# npm registry, so the backend checks run on the host instead:
#
#   - the preinstalled PostgreSQL 16 cluster, started and tuned like the local `db`
#     service (fsync, full_page_writes, synchronous_commit off), with a superuser role
#     `app` / password `throwaway` - a throwaway cluster, never a secret;
#   - a Python 3.12 venv in /opt/bss-venv (outside the repo) installed exactly like the
#     dev image: pip==26.2.1, requirements*.txt with constraints*.txt; pip gives up on a
#     stalled download after 30 s (one hung for 10+ minutes without a timeout);
#   - with BSS_CLOUD=1 (cloud mode, the guard then allows npm/npx/node on the host): the
#     UI's dependencies, `npm ci` in frontend/ with Node 22 (the version of the web
#     image) through frontend/scripts/install-deps.sh - the same step as make web-install,
#     so check-deps.sh and the lockfile hash work as in the container.
#
# Needs no secrets and never reads .env*. Idempotent: a second run only re-checks.
# Usage: bash scripts/cloud-setup.sh   (then the commands it prints at the end)
set -euo pipefail

VENV=/opt/bss-venv
PG_VERSION=16
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[[ "$(id -u)" == 0 ]] || { echo "cloud-setup: run as root (the cloud container's user)" >&2; exit 1; }
command -v pg_ctlcluster >/dev/null || { echo "cloud-setup: no postgresql-common (pg_ctlcluster)" >&2; exit 1; }
command -v python3.12 >/dev/null || { echo "cloud-setup: python3.12 not found" >&2; exit 1; }

# --- PostgreSQL 16 ---
if ! pg_lsclusters -h | awk -v v="$PG_VERSION" '$1 == v && $2 == "main"' | grep -q .; then
  pg_createcluster "$PG_VERSION" main
fi
pg_ctlcluster "$PG_VERSION" main start >/dev/null 2>&1 || true # "already running" is fine
pg_isready -q -h localhost || { echo "cloud-setup: PostgreSQL $PG_VERSION did not start" >&2; exit 1; }
psql_admin() { su postgres -c "psql -v ON_ERROR_STOP=1 -qtA $*"; }
if [[ "$(psql_admin "-c \"SELECT 1 FROM pg_roles WHERE rolname = 'app'\"")" != 1 ]]; then
  psql_admin "-c \"CREATE ROLE app LOGIN SUPERUSER PASSWORD 'throwaway'\""
fi
if [[ "$(psql_admin "-c \"SELECT 1 FROM pg_database WHERE datname = 'app'\"")" != 1 ]]; then
  su postgres -c "createdb -O app app"
fi
if [[ "$(psql_admin "-c 'SHOW fsync'")" != off ]]; then
  # As compose.override.yaml's db: every test clones and drops a database. Not crash-safe;
  # this cluster is throwaway. max_connections: pytest -n auto on every core.
  psql_admin "-c 'ALTER SYSTEM SET fsync = off' -c 'ALTER SYSTEM SET full_page_writes = off' \
    -c 'ALTER SYSTEM SET synchronous_commit = off' -c 'ALTER SYSTEM SET max_connections = 200'"
  pg_ctlcluster "$PG_VERSION" main restart
fi

# --- Python 3.12 venv, pinned exactly like the dev image (Dockerfile: builder + dev) ---
if [[ ! -x "$VENV/bin/python" ]]; then
  python3.12 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --timeout 30 pip==26.2.1
"$VENV/bin/pip" install -q --timeout 30 -r "$ROOT/requirements.txt" -c "$ROOT/constraints.txt"
"$VENV/bin/pip" install -q --timeout 30 -r "$ROOT/requirements-dev.txt" -c "$ROOT/constraints-dev.txt"
if ! diff <(grep -Ev '^(#|$)' "$ROOT/constraints-dev.txt" | sort -f) <("$VENV/bin/pip" freeze | sort -f) >/dev/null; then
  echo "cloud-setup: the venv's pip freeze differs from constraints-dev.txt" >&2
  exit 1
fi

# --- UI dependencies, cloud mode only (the workstation installs them in the web container) ---
if [[ "${BSS_CLOUD:-}" == 1 ]]; then
  [[ -x /opt/node22/bin/node ]] && export PATH="/opt/node22/bin:$PATH"
  node_major="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
  [[ "${node_major}" == 22 ]] || { echo "cloud-setup: Node 22 not found (got '${node_major:-none}')" >&2; exit 1; }
  if ! (cd "$ROOT/frontend" && sh scripts/check-deps.sh >/dev/null 2>&1); then
    (cd "$ROOT/frontend" && sh scripts/install-deps.sh)
  fi
  (cd "$ROOT/frontend" && sh scripts/check-deps.sh)
fi

cat <<'EOF'
cloud-setup: ready (PostgreSQL 16 on localhost:5432, venv /opt/bss-venv = constraints-dev.txt).
Backend checks, from the repo root (see docs/cloud.md):
  export PATH=/opt/bss-venv/bin:$PATH DATABASE_URL=postgresql://app:throwaway@localhost:5432/app APP_ENV=dev
  ruff check app tests alembic && ruff format --check app tests alembic   # = make lint
  pytest -n auto -p no:cacheprovider                                       # = make test
EOF
if [[ "${BSS_CLOUD:-}" == 1 ]]; then
  cat <<'EOF'
UI checks (cloud mode, node_modules = package-lock.json):
  export PATH=/opt/node22/bin:$PATH SPEC_DIR=../spec   # the container mounts spec/ at /spec
  (cd frontend && sh scripts/check-deps.sh && npm run lint)   # = make web-lint
  (cd frontend && sh scripts/check-deps.sh && npm test)       # = make web-test
EOF
fi
