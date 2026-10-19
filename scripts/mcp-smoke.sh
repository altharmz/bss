#!/usr/bin/env bash
# The MCP connector as an MCP client first meets it: discovery, the 401 challenge, the
# OAuth endpoints - and, through the shared Caddy, that exactly those paths reach the
# connector and everything else the bss site.
# Usage: scripts/mcp-smoke.sh <base url> [local|caddy]
#   local: http://localhost:8090 (make mcp-smoke)
#   caddy: https://bss2811.duckdns.org (deploy.sh, after the web smoke test)
#
# Sends no credential of any kind, so there is nothing to leak; changes nothing (the POSTs
# are refused before anything is stored).
set -euo pipefail

BASE="${1:?usage: mcp-smoke.sh <base url> [local|caddy]}"
BASE="${BASE%/}"
MODE="${2:-local}"
[[ "${MODE}" == local || "${MODE}" == caddy ]] || { echo "mode must be local or caddy" >&2; exit 2; }
OUT="$(mktemp)"
HEADERS="$(mktemp)"
trap 'rm -f "${OUT}" "${HEADERS}"' EXIT

fail() { printf '\033[1;31mMCP SMOKE FAILED:\033[0m %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok  %s\n' "$*"; }

# call <method> <path> [content type] [body] -> status code; body in $OUT, headers in $HEADERS
call() {
  local method="$1" path="$2" type="${3:-}" body="${4:-}"
  local args=(-sS --max-time 15 -o "${OUT}" -D "${HEADERS}" -w '%{http_code}' -X "${method}")
  [[ -n "${type}" ]] && args+=(-H "Content-Type: ${type}" -H "Accept: application/json, text/event-stream")
  [[ -n "${body}" ]] && args+=(--data "${body}")
  curl "${args[@]}" "${BASE}${path}" || echo "000"
}
header() { grep -i "^$1:" "${HEADERS}" | head -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//'; }
json() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${OUT}" "$1" 2>/dev/null; }
basic_challenge() { header WWW-Authenticate | grep -qi '^basic'; }

# 1. protected-resource metadata (RFC 9728), at the path the 401 names
code="$(call GET /.well-known/oauth-protected-resource/mcp)"
[[ "${code}" == 200 ]] || fail "protected-resource metadata answered ${code}"
[[ "$(json resource)" == "${BASE}/mcp" ]] || fail "resource is $(json resource), expected ${BASE}/mcp"
ok "GET /.well-known/oauth-protected-resource/mcp -> 200, resource ${BASE}/mcp"
code="$(call GET /.well-known/oauth-protected-resource)"
[[ "${code}" == 200 ]] || fail "root protected-resource metadata answered ${code}"
ok "GET /.well-known/oauth-protected-resource -> 200"

# 2. authorization-server metadata (RFC 8414): the issuer exactly, no trailing slash
code="$(call GET /.well-known/oauth-authorization-server)"
[[ "${code}" == 200 ]] || fail "authorization-server metadata answered ${code}"
[[ "$(json issuer)" == "${BASE}" ]] || fail "issuer is $(json issuer), expected ${BASE}"
[[ "$(json registration_endpoint)" == "${BASE}/register" ]] || fail "no registration endpoint"
ok "GET /.well-known/oauth-authorization-server -> 200, issuer ${BASE}"

# 3. /mcp without a token: the Bearer challenge pointing to the metadata - not Basic
code="$(call POST /mcp application/json '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')"
[[ "${code}" == 401 ]] || fail "POST /mcp without a token answered ${code}"
challenge="$(header WWW-Authenticate)"
[[ "${challenge}" == Bearer* ]] || fail "POST /mcp challenge is not Bearer: ${challenge}"
[[ "${challenge}" == *"resource_metadata=\"${BASE}/.well-known/oauth-protected-resource/mcp\""* ]] \
  || fail "POST /mcp challenge does not name the metadata: ${challenge}"
ok "POST /mcp without a token -> 401 Bearer, resource_metadata"

# 4. no OpenID Connect: a plain 404 from the connector, not a Basic challenge
code="$(call GET /.well-known/openid-configuration)"
[[ "${code}" == 404 ]] && ! basic_challenge || fail "openid-configuration answered ${code}"
ok "GET /.well-known/openid-configuration -> 404"

# 5. the OAuth endpoints answer themselves (no Basic challenge in front of them)
code="$(call GET '/login?request=nonexistent')"
[[ "${code}" == 400 ]] && grep -q 'no longer valid' "${OUT}" || fail "GET /login answered ${code}"
ok "GET /login with an unknown request -> 400 (the connector's page)"
for path in /token /revoke /register; do
  if [[ "${path}" == /register ]]; then
    code="$(call POST "${path}" application/json '{}')"
  else
    code="$(call POST "${path}" application/x-www-form-urlencoded 'grant_type=x')"
  fi
  [[ "${code}" =~ ^4 ]] && [[ "${code}" != 404 ]] && ! basic_challenge \
    || fail "POST ${path} answered ${code} $(header WWW-Authenticate)"
  ok "POST ${path} (empty) -> ${code} from the connector"
done
code="$(call GET '/authorize?client_id=nonexistent&response_type=code&code_challenge=x')"
[[ "${code}" == 400 ]] && ! basic_challenge || fail "GET /authorize answered ${code}"
ok "GET /authorize for an unknown client -> 400"

# 6. through Caddy (no login since U11): the carve-outs are exact and case-sensitive -
#    every other path is the bss site (the UI's index.html), not the connector
if [[ "${MODE}" == caddy ]]; then
  for path in / /MCP /Mcp /mcp/x /authorize/x /.well-known/OAUTH-AUTHORIZATION-SERVER; do
    code="$(call GET "${path}")"
    [[ "${code}" == 200 ]] && grep -q 'name="bss-build"' "${OUT}" && ! basic_challenge \
      || fail "GET ${path} answered ${code} without the UI's index.html (a carve-out too wide?)"
  done
  ok "/, /MCP, /Mcp, /mcp/x, /authorize/x, an upper-case well-known -> the UI, no Basic"
  # the public URL is reached over 443/TCP only: no HTTP/3 advertised for it
  call GET /.well-known/oauth-authorization-server >/dev/null
  [[ "$(header Alt-Svc)" != *h3* ]] || fail "Alt-Svc advertises HTTP/3: $(header Alt-Svc)"
  ok "no HTTP/3 in Alt-Svc"
fi

printf 'mcp smoke: all checks passed (%s, %s)\n' "${BASE}" "${MODE}"
