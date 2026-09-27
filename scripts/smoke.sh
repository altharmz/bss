#!/usr/bin/env bash
# Minimal HTTP contract check. Usage: scripts/smoke.sh http://localhost:8080
set -euo pipefail

BASE="${1:-http://localhost:8080}"
fail() { printf '\033[1;31mSMOKE FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

code="$(curl -sS -o /tmp/smoke.out -w '%{http_code}' --max-time 5 "${BASE}/healthz")" \
  || fail "cannot reach ${BASE}/healthz"
[[ "${code}" == "200" ]] || fail "/healthz returned ${code}: $(cat /tmp/smoke.out)"
grep -q '"db": *"up"' /tmp/smoke.out || fail "/healthz reports the database is down: $(cat /tmp/smoke.out)"

code="$(curl -sS -o /tmp/smoke.out -w '%{http_code}' --max-time 5 "${BASE}/")" \
  || fail "cannot reach ${BASE}/"
[[ "${code}" == "200" ]] || fail "/ returned ${code}: $(cat /tmp/smoke.out)"
grep -q '"status": *"ok"' /tmp/smoke.out || fail "/ did not report status ok: $(cat /tmp/smoke.out)"

printf '\033[1;32mSMOKE OK\033[0m %s\n' "${BASE}"
