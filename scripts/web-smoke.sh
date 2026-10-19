#!/usr/bin/env bash
# The admin UI and its API routes through the shared Caddy, as a browser sees them.
# Usage: SMOKE_BASIC_AUTH=smoke:<password> BSS_SMOKE_SECRET=<hex> \
#          scripts/web-smoke.sh <base url> <expected build>
#   e.g. scripts/web-smoke.sh https://vpn2811.duckdns.org:8445 1a02efa
#
# The credentials are read from the environment and handed to curl as a config file on a
# file descriptor - never on the command line (ps) and never printed. The `smoke` user is
# GET-only in Caddy; check 6 proves that. Behind Caddy the app authenticates too
# (docs/decisions/users.md): the smoke secret goes to it as X-Bss-Service, read-only there.
set -euo pipefail

BASE="${1:?usage: web-smoke.sh <base url> <expected build>}"
BUILD="${2:?usage: web-smoke.sh <base url> <expected build>}"
: "${SMOKE_BASIC_AUTH:?SMOKE_BASIC_AUTH (smoke:<password>) must be set}"
: "${BSS_SMOKE_SECRET:?BSS_SMOKE_SECRET must be set}"
OUT="$(mktemp)"
HEADERS="$(mktemp)"
trap 'rm -f "${OUT}" "${HEADERS}"' EXIT

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
write-out = \"%{http_code}\"
request = \"${method}\""
  # basic: Caddy's login only; auth: also the app's smoke credential
  if [[ "${who}" == auth || "${who}" == basic ]]; then
    config+=$'\n'"user = \"${SMOKE_BASIC_AUTH}\""
  fi
  if [[ "${who}" == auth ]]; then
    config+=$'\n'"header = \"X-Bss-Service: ${BSS_SMOKE_SECRET}\""
  fi
  if [[ "${method}" != GET ]]; then
    config+=$'\n'"header = \"Content-Type: application/json\""$'\n'"data = \"{}\""
  fi
  curl -K <(printf '%s\n' "${config}") "${BASE}${path}" || echo "000"
}
header() { grep -i "^$1:" "${HEADERS}" | head -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }
is_json() { python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${OUT}" 2>/dev/null; }

# 1. basic auth is on for the UI
code="$(get anon GET /)"
[[ "${code}" == 401 ]] || fail "GET / without credentials answered ${code}, expected 401"
ok "GET / without credentials -> 401"

# 2. index.html of THIS build is served
code="$(get auth GET /)"
[[ "${code}" == 200 ]] || fail "GET / answered ${code}"
[[ "$(header Content-Type)" == text/html* ]] || fail "GET / is not HTML: $(header Content-Type)"
grep -q "<meta name=\"bss-build\" content=\"${BUILD}\"" "${OUT}" \
  || fail "GET / does not carry build ${BUILD} (an old index.html is live?)"
ok "GET / -> 200 text/html, bss-build ${BUILD}"
asset="$(grep -oE '/assets/[A-Za-z0-9._-]+\.js' "${OUT}" | head -1)"
[[ -n "${asset}" ]] || fail "index.html references no /assets/*.js"

# 3. a deep link is answered by index.html too (try_files), and a hashed asset is served
code="$(get auth GET /subscribers)"
[[ "${code}" == 200 ]] && grep -q 'name="bss-build"' "${OUT}" \
  || fail "GET /subscribers answered ${code} without index.html (try_files missing?)"
ok "GET /subscribers -> index.html (deep link)"
code="$(get auth GET "${asset}")"
[[ "${code}" == 200 ]] || fail "GET ${asset} answered ${code}"
ok "GET ${asset} -> 200 (cache-control: $(header Cache-Control))"

# 4. /internal goes to the app: JSON, not the index.html fallback. Caddy's login alone is
#    not enough - the app answers 401 - and with the smoke credential the app names the
#    operator `smoke`, the same name as the basic-auth user
code="$(get basic GET /internal/v1/context)"
[[ "${code}" == 401 ]] || fail "GET /internal/v1/context with Caddy's login only answered ${code}, expected 401 from the app"
ok "GET /internal/v1/context without the app's credential -> 401"
code="$(get auth GET /internal/v1/context)"
[[ "${code}" == 200 ]] || fail "GET /internal/v1/context answered ${code}"
[[ "$(header Content-Type)" == application/json* ]] \
  || fail "GET /internal/v1/context is not JSON ($(header Content-Type)) - /internal/* not routed to the app?"
is_json || fail "GET /internal/v1/context body is not JSON"
operator="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("operator"))' "${OUT}")"
[[ "${operator}" == "${SMOKE_BASIC_AUTH%%:*}" ]] \
  || fail "operator is ${operator}, expected ${SMOKE_BASIC_AUTH%%:*} - smoke credential not recognised by the app?"
ok "GET /internal/v1/context -> 200 JSON, operator ${operator}"

# 5. /tmf-api goes to the app
code="$(get auth GET '/tmf-api/accountManagement/v5/billingAccount?limit=1')"
[[ "${code}" == 200 ]] || fail "GET /tmf-api/.../billingAccount answered ${code}"
[[ "$(header Content-Type)" == application/json* ]] && is_json \
  || fail "GET /tmf-api/.../billingAccount is not JSON"
[[ -n "$(header X-Total-Count)" ]] || fail "GET /tmf-api/.../billingAccount has no X-Total-Count"
ok "GET /tmf-api/accountManagement/v5/billingAccount -> 200 JSON"

# 6. the smoke user cannot change anything
code="$(get auth POST /tmf-api/productOrderingManagement/v5/productOrder)"
[[ "${code}" == 403 ]] || fail "POST as the smoke user answered ${code}, expected 403"
ok "POST as smoke -> 403"

# 7. /healthz stays open for the external monitor
code="$(get anon GET /healthz)"
[[ "${code}" == 200 ]] || fail "GET /healthz without credentials answered ${code}, expected 200"
ok "GET /healthz without credentials -> 200"

printf '\033[1;32mWEB SMOKE OK\033[0m %s (build %s)\n' "${BASE}" "${BUILD}"
