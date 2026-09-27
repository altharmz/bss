#!/usr/bin/env bash
# Restore a pg_dump produced by backup.sh onto the VPS database, over the same SSH
# connection backup.sh and deploy.sh use - no new connection method. Never runs on its
# own: always asks a human to confirm by typing "yes" after showing what is about to be
# overwritten, and always loads the dump inside a single transaction so a failure partway
# through rolls back instead of leaving the database half-restored.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
REMOTE_DIR="${REMOTE_DIR:-lab}"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
FILE="${FILE:?usage: make restore FILE=backups/vps-lab-<timestamp>.sql.gz}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -f "${FILE}" ]] || fail "no such file: ${FILE}"

row_count() {
  ssh "${REMOTE}" "cd ${REMOTE_DIR} && ${COMPOSE[*]} exec -T db sh -c 'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -tAc \"SELECT count(*) FROM $1\"'" \
    | tr -d '[:space:]'
}

players_before="$(row_count players)" || fail "could not read current row counts from ${REMOTE}"
games_before="$(row_count games)" || fail "could not read current row counts from ${REMOTE}"

printf '\nAbout to restore onto %s:\n' "${REMOTE}"
printf '  file:            %s\n' "${FILE}"
printf '  players (before): %s\n' "${players_before}"
printf '  games (before):   %s\n' "${games_before}"
printf '\nThis overwrites the current database on %s. It cannot be undone by this script.\n' "${REMOTE}"
read -r -p "Type 'yes' to continue: " confirm
[[ "${confirm}" == "yes" ]] || fail "aborted (typed '${confirm}', expected 'yes')"

log "Restoring ${FILE} onto ${REMOTE} inside a single transaction"
gunzip -c "${FILE}" \
  | ssh "${REMOTE}" "cd ${REMOTE_DIR} && ${COMPOSE[*]} exec -T db sh -c 'psql -v ON_ERROR_STOP=1 --single-transaction -U \$POSTGRES_USER -d \$POSTGRES_DB'" \
  || fail "restore failed and was rolled back (single transaction)"

log "Restore complete"
