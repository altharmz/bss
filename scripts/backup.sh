#!/usr/bin/env bash
# pg_dump the database on the VPS (run inside the db container, over the existing SSH
# connection - no new port or connection method) and pull the compressed dump down to
# backups/ locally. Never runs `docker build` or touches anything but the db container.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
REMOTE_DIR="${REMOTE_DIR:-lab}"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p backups
ts="$(date +%Y%m%d-%H%M%S)"
out="backups/__PROJECT__-${ts}.sql.gz"

log "Dumping database on ${REMOTE} to ${out}"
# --clean --if-exists so the dump can be loaded back onto a database that already has the
# current schema (restore drops each object before recreating it) instead of failing on
# "already exists". $POSTGRES_USER/$POSTGRES_DB are read from the db container's own
# environment (already set by compose), not passed in from here.
ssh "${REMOTE}" "cd ${REMOTE_DIR} && ${COMPOSE[*]} exec -T db sh -c 'pg_dump -U \$POSTGRES_USER -d \$POSTGRES_DB --clean --if-exists'" \
  | gzip -1 > "${out}" \
  || { rm -f "${out}"; fail "pg_dump failed"; }

log "Wrote ${out} ($(du -h "${out}" | cut -f1))"
