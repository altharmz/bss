#!/usr/bin/env bash
# The admin UI and its API routes through the shared Caddy, as a browser sees them.
# Usage: BSS_SMOKE_SECRET=<hex> scripts/web-smoke.sh <base url> <expected build>
#   e.g. scripts/web-smoke.sh https://vpn2811.duckdns.org:8445 1a02efa
#
# Caddy asks for no login (vps-infra U11); the app is the only boundary
# (docs/decisions/users.md). The smoke secret goes to it as X-Bss-Service, read-only
# there - check 6 proves that. It is read from the environment and handed to curl as a
# config file on a file descriptor - never on the command line (ps) and never printed.
set -euo pipefail

BASE="${1:?usage: web-smoke.sh <base url> <expected build>}"
BUILD="${2:?usage: web-smoke.sh <base url> <expected build>}"
: "${BSS_SMOKE_SECRET:?BSS_SMOKE_SECRET must be set}"
OUT="$(mktemp)"
HEADERS="$(mktemp)"
trap 'rm -f "${OUT}" "${HEADERS}"' EXIT

# shellcheck source=scripts/smoke-retry.sh
source "$(dirname "$0")/smoke-retry.sh"

fail() { printf '\033[1;31mWEB SMOKE FAILED:\033[0m %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok  %s\n' "$*"; }

# get <auth|anon> <method> <path> -> status code; body in $OUT, headers in $HEADERS
get() {
  local who="$1" method="$2" path="$3" config
  config="max-time = 15
silent
show-error
output = \"${OUT}\"
dump-header = \"${HEADERS}\"
write-out = \"%{http_code} %{time_pretransfer}\"
request = \"${method}\""
  if [[ "${who}" == auth ]]; then
    config+=$'\n'"header = \"X-Bss-Service: ${BSS_SMOKE_SECRET}\""
  fi
  if [[ "${method}" != GET ]]; then
    config+=$'\n'"header = \"Content-Type: application/json\""$'\n'"data = \"{}\""
  fi
  # a fresh config descriptor for each attempt (smoke-retry.sh)
  get_once() { curl -K <(printf '%s\n' "${config}") "${BASE}${path}"; }
  smoke_retry "${method}" "${path}" get_once
}
header() { grep -i "^$1:" "${HEADERS}" | head -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }
is_json() { python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${OUT}" 2>/dev/null; }

# 1. index.html of THIS build is served, without credentials (the UI's sign-in is in it)
code="$(get anon GET /)"
[[ "${code}" == 200 ]] || fail "GET / answered ${code}"
[[ "$(header Content-Type)" == text/html* ]] || fail "GET / is not HTML: $(header Content-Type)"
grep -q "<meta name=\"bss-build\" content=\"${BUILD}\"" "${OUT}" \
  || fail "GET / does not carry build ${BUILD} (an old index.html is live?)"
ok "GET / -> 200 text/html, bss-build ${BUILD}"
asset="$(grep -oE '/assets/[A-Za-z0-9._-]+\.js' "${OUT}" | head -1)"
[[ -n "${asset}" ]] || fail "index.html references no /assets/*.js"

# 2. a deep link is answered by index.html too (try_files), and a hashed asset is served
code="$(get anon GET /subscribers)"
[[ "${code}" == 200 ]] && grep -q 'name="bss-build"' "${OUT}" \
  || fail "GET /subscribers answered ${code} without index.html (try_files missing?)"
ok "GET /subscribers -> index.html (deep link)"
code="$(get anon GET "${asset}")"
[[ "${code}" == 200 ]] || fail "GET ${asset} answered ${code}"
ok "GET ${asset} -> 200 (cache-control: $(header Cache-Control))"

# 3. /internal goes to the app, which answers 401 JSON without a credential - not the
#    index.html fallback
code="$(get anon GET /internal/v1/context)"
[[ "${code}" == 401 ]] || fail "GET /internal/v1/context without credentials answered ${code}, expected 401 from the app"
[[ "$(header Content-Type)" == application/json* ]] && is_json \
  || fail "GET /internal/v1/context without credentials is not JSON ($(header Content-Type)) - /internal/* not routed to the app?"
ok "GET /internal/v1/context without credentials -> 401 JSON"

# 4. with the smoke secret the app names the operator `smoke`
code="$(get auth GET /internal/v1/context)"
[[ "${code}" == 200 ]] || fail "GET /internal/v1/context answered ${code}"
[[ "$(header Content-Type)" == application/json* ]] && is_json \
  || fail "GET /internal/v1/context is not JSON ($(header Content-Type))"
operator="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("operator"))' "${OUT}")"
[[ "${operator}" == smoke ]] \
  || fail "operator is ${operator}, expected smoke - smoke secret not recognised by the app?"
ok "GET /internal/v1/context -> 200 JSON, operator ${operator}"

# 5. /tmf-api goes to the app
code="$(get auth GET '/tmf-api/accountManagement/v5/billingAccount?limit=1')"
[[ "${code}" == 200 ]] || fail "GET /tmf-api/.../billingAccount answered ${code}"
[[ "$(header Content-Type)" == application/json* ]] && is_json \
  || fail "GET /tmf-api/.../billingAccount is not JSON"
# lists page without counts (#312): X-Result-Count, never X-Total-Count
[[ -n "$(header X-Result-Count)" ]] || fail "GET /tmf-api/.../billingAccount has no X-Result-Count"
[[ -z "$(header X-Total-Count)" ]] || fail "GET /tmf-api/.../billingAccount still counts (X-Total-Count)"
ok "GET /tmf-api/accountManagement/v5/billingAccount -> 200 JSON"

# 6. the smoke secret cannot change anything (the app's 403)
code="$(get auth POST /tmf-api/productOrderingManagement/v5/productOrder)"
[[ "${code}" == 403 ]] || fail "POST with the smoke secret answered ${code}, expected 403"
ok "POST with the smoke secret -> 403"

# 7. /healthz stays open for the external monitor
code="$(get anon GET /healthz)"
[[ "${code}" == 200 ]] || fail "GET /healthz without credentials answered ${code}, expected 200"
ok "GET /healthz without credentials -> 200"

printf '\033[1;32mWEB SMOKE OK\033[0m %s (build %s)\n' "${BASE}" "${BUILD}"
