#!/usr/bin/env bash
# Read-only: lists the automated daily dumps sitting on the VPS (written there by
# cron-backup.sh). Does not pull anything down - for a local copy use `make backup`.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
REMOTE_DIR="${REMOTE_DIR:-lab}"

ssh "${REMOTE}" "cd ${REMOTE_DIR} && ls -lh backups/ 2>/dev/null | grep '\.sql\.gz\$' || echo 'no backups found yet'"
