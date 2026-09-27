#!/usr/bin/env bash
# Lives on the VPS at ~/lab/cron-backup.sh (shipped there by scripts/deploy.sh, installed
# into crontab by `make cron-install`). Runs locally on the VPS - no ssh hop - dumping the
# database from inside the db container the same way scripts/backup.sh does from the
# workstation, then rotating old dumps. Never touches VPN ports or other containers.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
KEEP=7

mkdir -p backups
ts="$(date +%Y%m%d-%H%M%S)"
out="backups/vps-lab-${ts}.sql.gz"
trap 'rm -f "${out}.tmp"' EXIT

# --clean --if-exists so the dump can be loaded back onto a database that already has the
# current schema. $POSTGRES_USER/$POSTGRES_DB come from the db container's own
# environment (set by compose), not from anything passed in here.
"${COMPOSE[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  | gzip -1 > "${out}.tmp"
mv "${out}.tmp" "${out}"

# keep the KEEP most recent dumps, delete the rest
ls -1t backups/vps-lab-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
