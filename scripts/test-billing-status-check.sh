#!/usr/bin/env bash
# Cases for scripts/billing-status-check.sh, both ways. Run by `make test` (on the host,
# milliseconds). The bodies are what app/status.py answers through JSONResponse (compact
# JSON); one spaced variant keeps the pattern independent of the serializer's spacing.
set -uo pipefail

check="$(dirname "$0")/billing-status-check.sh"
total=0
failed=0

expect() { # expect <accept|reject> <http code> <body>
  local want="$1" code="$2" body="$3" got
  if "${check}" "${code}" <<<"${body}"; then got=accept; else got=reject; fi
  total=$((total + 1))
  if [[ "${got}" != "${want}" ]]; then
    printf 'FAIL expected %s, got %s: %s %s\n' "${want}" "${got}" "${code}" "${body}" >&2
    failed=$((failed + 1))
  fi
}

# every answer app/status.py can give
expect accept 200 '{"date":"2026-10-09","status":"ok","ageSeconds":3600}'
expect accept 200 '{"date":"2026-10-09","status":"running","ageSeconds":12}'
expect accept 503 '{"date":"2026-10-07","status":"stale","ageSeconds":180000}'
expect accept 503 '{"date":"2026-10-09","status":"failed","ageSeconds":60}'
expect accept 503 '{"date":"2026-10-09","status":"running","ageSeconds":7300}'
expect accept 503 '{"date":null,"status":"none","ageSeconds":null}'
expect accept 200 '{"date": "2026-10-09", "status": "running", "ageSeconds": 12}'
# a status with the wrong code, an unknown status, another code, no body
expect reject 200 '{"date":"2026-10-07","status":"stale","ageSeconds":180000}'
expect reject 200 '{"date":"2026-10-09","status":"failed","ageSeconds":60}'
expect reject 200 '{"date":null,"status":"none","ageSeconds":null}'
expect reject 503 '{"date":"2026-10-09","status":"ok","ageSeconds":3600}'
expect reject 200 '{"date":"2026-10-09","status":"done","ageSeconds":1}'
expect reject 200 '{"date":"2026-10-09","status":"running-late","ageSeconds":1}'
expect reject 500 '{"date":"2026-10-09","status":"ok","ageSeconds":1}'
expect reject 404 '{"@type":"Error","code":"404","reason":"Not Found","status":"404"}'
expect reject 200 ''
expect reject '' '{"date":"2026-10-09","status":"ok","ageSeconds":1}'

if ((failed > 0)); then
  printf 'billing-status check: %d of %d cases failed\n' "${failed}" "${total}" >&2
  exit 1
fi
printf 'billing-status check: all %d cases pass\n' "${total}"
