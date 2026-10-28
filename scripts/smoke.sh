#!/usr/bin/env bash
# Minimal HTTP contract check. Usage: scripts/smoke.sh http://localhost:8081
set -euo pipefail

BASE="${1:-http://localhost:8081}"
fail() { printf '\033[1;31mSMOKE FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

code="$(curl -sS -o /tmp/smoke.out -w '%{http_code}' --max-time 5 "${BASE}/healthz")" \
  || fail "cannot reach ${BASE}/healthz"
[[ "${code}" == "200" ]] || fail "/healthz returned ${code}: $(cat /tmp/smoke.out)"
grep -q '"db": *"up"' /tmp/smoke.out || fail "/healthz reports the database is down: $(cat /tmp/smoke.out)"

code="$(curl -sS -o /tmp/smoke.out -w '%{http_code}' --max-time 5 "${BASE}/")" \
  || fail "cannot reach ${BASE}/"
[[ "${code}" == "200" ]] || fail "/ returned ${code}: $(cat /tmp/smoke.out)"
grep -q '"status": *"ok"' /tmp/smoke.out || fail "/ did not report status ok: $(cat /tmp/smoke.out)"

# The billing freshness signal: 503 is a valid answer (stale, failed, no run yet), so only
# the shape is checked - 200 ok or running, 503 one of the other known statuses
# (scripts/billing-status-check.sh, shared with deploy.sh).
code="$(curl -sS -o /tmp/smoke.out -w '%{http_code}' --max-time 5 "${BASE}/billing-status")" \
  || fail "cannot reach ${BASE}/billing-status"
"$(dirname "$0")/billing-status-check.sh" "${code}" </tmp/smoke.out \
  || fail "/billing-status returned ${code}: $(cat /tmp/smoke.out)"

printf '\033[1;32mSMOKE OK\033[0m %s\n' "${BASE}"
