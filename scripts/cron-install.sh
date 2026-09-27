#!/usr/bin/env bash
# Installs (or refreshes) the daily backup cron job in the deploy user's crontab on the
# VPS: 02:30, half an hour before the documented 03:00 nightly reboot. Idempotent -
# re-running replaces the existing line instead of adding a duplicate.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
REMOTE_DIR="${REMOTE_DIR:-lab}"
CRON_LINE="30 2 * * * cd ${REMOTE_DIR} && ./cron-backup.sh >> backups/cron-backup.log 2>&1"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

# So the log redirect below has somewhere to write on the very first firing - cron's
# shell creates the redirect target before running the command, and cron-backup.sh's own
# mkdir -p only runs after that.
ssh "${REMOTE}" "mkdir -p ${REMOTE_DIR}/backups"

log "Installing daily backup cron job (02:30) on ${REMOTE}"
ssh "${REMOTE}" "{ crontab -l 2>/dev/null | grep -vF 'cron-backup.sh'; echo '${CRON_LINE}'; } | crontab -" \
  || fail "could not update crontab on ${REMOTE}"

log "Current crontab on ${REMOTE}:"
ssh "${REMOTE}" "crontab -l"
