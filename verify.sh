#!/usr/bin/env bash
# Exits non-zero unless the app is running and responding on the VPS.
set -euo pipefail
APP="${1:-demo}"
PORT="${2:-8080}"
DIR="~/src/vps-lab/app/${APP}"

fail() {
  echo "FAIL: $1"
  ssh vps-test "cd ${DIR} && docker compose logs --tail=50" || true
  exit 1
}

ssh vps-test "cd ${DIR} && docker compose ps --status running -q" | grep -q . \
  || fail "no running container for ${APP}"

ssh vps-test "curl -fsS --max-time 5 http://localhost:${PORT}/ >/dev/null" \
  || fail "${APP} not responding on :${PORT}"

REMOTE=$(ssh vps-test "cd ~/src/vps-lab && git rev-parse --short HEAD")
LOCAL=$(git rev-parse --short HEAD)
echo "OK: ${APP} responding on :${PORT} — deployed ${REMOTE}, local ${LOCAL}"
[ "$REMOTE" = "$LOCAL" ] || echo "WARN: deployed SHA differs from local HEAD"
