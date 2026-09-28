#!/usr/bin/env bash
# Run once via `make init NAME=... APP_PORT=... HTTPS_PORT=... DOMAIN=...`. Substitutes the
# NAME/APP_PORT/HTTPS_PORT/DOMAIN placeholders everywhere in the tree, creates .env and
# .env.prod from .env.example with a freshly generated database password each, then
# removes the `init` target from the Makefile so a second run can't re-substitute an
# already-substituted tree.
set -euo pipefail

NAME="${1:?usage: scripts/init.sh NAME APP_PORT HTTPS_PORT DOMAIN}"
APP_PORT="${2:?usage: scripts/init.sh NAME APP_PORT HTTPS_PORT DOMAIN}"
HTTPS_PORT="${3:?usage: scripts/init.sh NAME APP_PORT HTTPS_PORT DOMAIN}"
DOMAIN="${4:?usage: scripts/init.sh NAME APP_PORT HTTPS_PORT DOMAIN}"

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

PLACEHOLDER_NAME="PROJECT"
PLACEHOLDER_APP_PORT="APP_PORT"
PLACEHOLDER_HTTPS_PORT="HTTPS_PORT"
PLACEHOLDER_DOMAIN="DOMAIN"

log "Substituting placeholders (NAME=${NAME} APP_PORT=${APP_PORT} HTTPS_PORT=${HTTPS_PORT} DOMAIN=${DOMAIN})"
mapfile -d '' files < <(
  grep -rlZ \
    -e "__${PLACEHOLDER_NAME}__" \
    -e "__${PLACEHOLDER_APP_PORT}__" \
    -e "__${PLACEHOLDER_HTTPS_PORT}__" \
    -e "__${PLACEHOLDER_DOMAIN}__" \
    --exclude-dir=.git --exclude=init.sh .
)
# | as the sed delimiter so a value containing / (a path-ish domain, say) can't break out.
for f in "${files[@]}"; do
  sed -i \
    -e "s|__${PLACEHOLDER_NAME}__|${NAME}|g" \
    -e "s|__${PLACEHOLDER_APP_PORT}__|${APP_PORT}|g" \
    -e "s|__${PLACEHOLDER_HTTPS_PORT}__|${HTTPS_PORT}|g" \
    -e "s|__${PLACEHOLDER_DOMAIN}__|${DOMAIN}|g" \
    "$f"
done

for env_file in .env .env.prod; do
  if [[ -f "${env_file}" ]]; then
    log "${env_file} already exists — leaving it alone"
  else
    log "Creating ${env_file}"
    pw="$(openssl rand -hex 24)"
    sed "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${pw}/" .env.example > "${env_file}"
  fi
done

log "Removing the init target from the Makefile (one-time use)"
sed -i '/# --- init:begin/,/# --- init:end/d' Makefile
sed -i '/^\.PHONY:/s/[[:space:]]*\binit\b//' Makefile

log "Done. Review the diff, then run: make up && make smoke"
printf '\n\033[1;33mIMPORTANT:\033[0m POSTGRES_PASSWORD in .env.prod is TEMPORARY - it is a random value that\n' >&2
printf 'the shared Postgres on the VPS does not know. Before the first deploy, run\n' >&2
printf '    make add-db NAME=%s\n' "${NAME}" >&2
printf 'in vps-infra and REPLACE POSTGRES_PASSWORD in .env.prod with the role password it\n' >&2
printf 'used: DB_PASSWORD_%s from vps-infra'"'"'s .env.prod.\n' "${NAME^^}" >&2
printf 'If the two differ, the deploy does not fail with a clear error: migrations hang on\n' >&2
printf 'connection retries until the 120s timeout. (.env is local-only and stays as it is.)\n' >&2
printf 'Also add this app'"'"'s block to vps-infra'"'"'s Caddyfile - see README.md.\n' >&2
