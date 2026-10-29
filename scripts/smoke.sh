#!/usr/bin/env bash
# Minimal HTTP contract check. Usage: scripts/smoke.sh http://localhost:8081
set -euo pipefail

BASE="${1:?usage: scripts/smoke.sh <base-url>}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fail() { printf '\033[1;31mSMOKE FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

code="$(curl -sS -o "$tmp/body" -w '%{http_code}' --max-time 5 "${BASE}/healthz")" \
  || fail "cannot reach ${BASE}/healthz"
[[ "${code}" == "200" ]] || fail "/healthz returned ${code}: $(cat "$tmp/body")"
grep -q '"db": *"up"' "$tmp/body" || fail "/healthz reports the database is down: $(cat "$tmp/body")"

code="$(curl -sS -o "$tmp/body" -w '%{http_code}' --max-time 5 "${BASE}/")" \
  || fail "cannot reach ${BASE}/"
[[ "${code}" == "200" ]] || fail "/ returned ${code}: $(cat "$tmp/body")"
grep -q '"status": *"ok"' "$tmp/body" || fail "/ did not report status ok: $(cat "$tmp/body")"

# The billing freshness signal: 503 is a valid answer (stale, failed, no run yet), so only
# the shape is checked - 200 ok or running, 503 one of the other known statuses
# (scripts/billing-status-check.sh, shared with deploy.sh).
code="$(curl -sS -o "$tmp/body" -w '%{http_code}' --max-time 5 "${BASE}/billing-status")" \
  || fail "cannot reach ${BASE}/billing-status"
"$(dirname "$0")/billing-status-check.sh" "${code}" <"$tmp/body" \
  || fail "/billing-status returned ${code}: $(cat "$tmp/body")"

printf '\033[1;32mSMOKE OK\033[0m %s\n' "${BASE}"
