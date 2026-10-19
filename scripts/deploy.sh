#!/usr/bin/env bash
# Build the image in WSL, ship it to the VPS, restart the stack there, wait until it is
# healthy. This is the ONLY script allowed to mutate state on the VPS.
#
# The VPS has 1 vCPU and runs a production VPN server, so nothing is ever built remotely.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
# Relative to the deploy user's home, i.e. ~/bss - one directory per project, so
# two apps from this template on the same VPS never overwrite each other's files.
REMOTE_DIR="${REMOTE_DIR:-bss}"
IMAGE_NAME="${IMAGE_NAME:-bss-app}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
KEEP_IMAGES=3
MIGRATE_TIMEOUT=120

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

# One SSH connection per deploy. Every ssh/rsync below is multiplexed over a single master
# opened up front: the VPS has been seen to stall *new* connections at the banner exchange
# (kex_exchange_identification: Connection timed out), and a deploy used to open ~15.
# ConnectTimeout also bounds the banner exchange, ServerAlive* detect a dead link, and
# BatchMode forbids prompts - every failure is a fast, visible exit instead of a hang.
SSH_OPTS=(
  -o ControlMaster=auto -o "ControlPath=${HOME}/.ssh/cm-%C" -o ControlPersist=60
  -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o BatchMode=yes
)
rssh() { ssh "${SSH_OPTS[@]}" "${REMOTE}" "$@"; }
RSYNC=(rsync -az --timeout=60 -e "ssh ${SSH_OPTS[*]}")

# The master is backgrounded, so its own errors (e.g. "Timeout, server not responding")
# would vanish. It logs verbosely to a file instead; on any failure the tail is printed.
MASTER_LOG="$(mktemp -t bss-deploy-ssh.XXXXXX)"
MASTER_OPTS=(-o ControlMaster=yes -o LogLevel=VERBOSE -E "${MASTER_LOG}")

opened_master=""
finish() {
  local rc=$?
  if [[ -n "${opened_master}" ]]; then
    ssh "${SSH_OPTS[@]}" -O exit "${REMOTE}" 2>/dev/null || true
  fi
  if (( rc != 0 )) && [[ -s "${MASTER_LOG}" ]]; then
    printf '\n--- ssh master log (last 20 lines) ---\n' >&2
    tail -n 20 "${MASTER_LOG}" >&2
  fi
  rm -f "${MASTER_LOG}"
}
trap finish EXIT

open_master() {
  if ssh "${SSH_OPTS[@]}" -O check "${REMOTE}" 2>/dev/null; then
    log "Reusing the existing SSH master connection to ${REMOTE}"
    return 0
  fi
  local rc=0
  ssh "${SSH_OPTS[@]}" "${MASTER_OPTS[@]}" -MNf "${REMOTE}" || rc=$?
  if (( rc == 255 )); then
    # 255 = ssh itself failed (connect/banner/auth), not a remote command. Retry once.
    log "Could not open the SSH connection to ${REMOTE} (exit 255) - retrying once in 5s"
    sleep 5
    rc=0
    ssh "${SSH_OPTS[@]}" "${MASTER_OPTS[@]}" -MNf "${REMOTE}" || rc=$?
  fi
  (( rc == 0 )) || fail "cannot open an SSH connection to ${REMOTE} (exit ${rc}, after 1 retry).
  Nothing on the VPS was changed. Check the network and sshd on ${REMOTE}."
  opened_master=1
  ssh "${SSH_OPTS[@]}" -O check "${REMOTE}" 2>/dev/null \
    || fail "SSH master connection to ${REMOTE} did not come up"
}

[[ -f .env.prod ]] || fail ".env.prod is missing (copy .env.example and fill it in)"

# TAG is the commit hash. A dirty tree would ship different content (image or compose
# files) under a clean-looking tag, so refuse before touching the network.
dirty="$(git status --porcelain)" || fail "not a git work tree - cannot tell what would be deployed"
[[ -z "${dirty}" ]] || fail "working tree is not clean - ${IMAGE_NAME}:${TAG} would not describe what ships.
Commit or stash first:
${dirty}"

# The web smoke test goes through the shared Caddy as the GET-only `smoke` user. Read only
# that one variable; it is handed to the smoke script in its environment, never printed.
#
# The credential lives in .env.smoke - local only: gitignored, never rsynced (the
# workstation-only counterpart of .env.prod, which IS shipped to the VPS as ~/bss/.env).
# SMOKE_ENV points elsewhere only for a rehearsal with a dummy file.
WEB_URL="${WEB_URL:-https://vpn2811.duckdns.org:8445}"
# The MCP connector's public URL: MCP_PUBLIC_URL in compose.prod.yaml (port 443 through the
# host's SNI router to Caddy's :8445). Its OAuth metadata names this URL only.
MCP_URL="${MCP_URL:-https://bss2811.duckdns.org}"
SMOKE_ENV="${SMOKE_ENV:-.env.smoke}"
[[ -f "${SMOKE_ENV}" ]] \
  || fail "${SMOKE_ENV} is missing: create .env.smoke with SMOKE_BASIC_AUTH=smoke:<password> (see .env.example and docs/vps-infra-change-request.md)"
SMOKE_BASIC_AUTH="$(sed -n 's/^SMOKE_BASIC_AUTH=//p' "${SMOKE_ENV}" | tail -n 1)"
[[ "${SMOKE_BASIC_AUTH}" == smoke:?* ]] \
  || fail "SMOKE_BASIC_AUTH=smoke:<password> is missing in ${SMOKE_ENV}"
! grep -q '^SMOKE_BASIC_AUTH=' .env.prod \
  || fail ".env.prod contains SMOKE_BASIC_AUTH: remove it - .env.prod is shipped to the VPS, the smoke credential belongs in .env.smoke only"
# The app's read-only smoke credential (docs/decisions/users.md): the app gets it from
# .env.prod, the web smoke from .env.smoke - the same value; a mismatch fails the smoke.
BSS_SMOKE_SECRET="$(sed -n 's/^BSS_SMOKE_SECRET=//p' "${SMOKE_ENV}" | tail -n 1)"
[[ -n "${BSS_SMOKE_SECRET}" ]] \
  || fail "BSS_SMOKE_SECRET is missing in ${SMOKE_ENV} (the same value as in .env.prod; see .env.example)"
grep -q '^BSS_SERVICE_SECRET=.' .env.prod \
  || fail "BSS_SERVICE_SECRET is empty or missing in .env.prod (see .env.example)"
grep -q '^BSS_SMOKE_SECRET=.' .env.prod \
  || fail "BSS_SMOKE_SECRET is empty or missing in .env.prod (see .env.example)"

# The MCP login needs at least one user; checked here, before any network step (the value
# is never printed). compose would refuse too, but only after the migration.
grep -q '^MCP_USERS=.' .env.prod \
  || fail "MCP_USERS is empty or missing in .env.prod (see .env.example; hashes: python -m bss_mcp.passwords)"

# What ships, in one place: the real deploy calls these with the VPS as destination,
# DRY_RUN=1 with --dry-run against a local scratch directory - the same calls.
# Chained with && so a failed rsync fails the function wherever it is called - set -e does
# not reach into a command substitution, which is where DRY_RUN calls them.
ship_config() { # <destination> [rsync options]
  local dest="$1"
  shift
  "${RSYNC[@]}" "$@" compose.yaml compose.prod.yaml "${dest}/" &&
    # compose auto-loads a file named .env from its project directory, so EVERY remote
    # compose call sees the secrets - not just the one that starts the stack
    "${RSYNC[@]}" "$@" .env.prod "${dest}/.env"
}
ship_web() { # <destination> [rsync options]
  local dest="$1"
  shift
  "${RSYNC[@]}" "$@" --exclude index.html frontend/dist/ "${dest}/web/" &&
    "${RSYNC[@]}" "$@" frontend/dist/index.html "${dest}/web/index.html" &&
    "${RSYNC[@]}" "$@" --delete frontend/dist/ "${dest}/web/"
}

# The admin UI is built locally too, before anything touches the network, with the commit
# as its bss-build marker - the web smoke test reads it back through Caddy.
log "Building the admin UI (frontend/dist, build ${TAG})"
make web-build WEB_BUILD="${TAG}" </dev/null || fail "the UI build failed (run make web-install if node_modules is out of date)"
grep -q "<meta name=\"bss-build\" content=\"${TAG}\"" frontend/dist/index.html \
  || fail "frontend/dist/index.html does not carry build ${TAG}"

# DRY_RUN=1: every local check above has passed; list exactly what the rsync calls of a
# real deploy would ship (the same functions, --dry-run into a local scratch directory),
# prove the smoke credential is not among it, and stop before any network step.
if [[ "${DRY_RUN:-}" == 1 ]]; then
  stage="$(mktemp -d)"
  mkdir -p "${stage}/web" # --dry-run creates nothing; the real web/ exists before ship_web
  listing="$(mktemp)"
  if ! { ship_config "${stage}" --dry-run --out-format='%n' &&
         ship_web "${stage}" --dry-run --out-format='web/%n'; } >"${listing}"; then
    rm -rf "${stage}" "${listing}"
    fail "dry run: an rsync call failed (see above)"
  fi
  shipped="$(sort -u "${listing}")"
  rm -rf "${stage}" "${listing}"
  log "Dry run: a deploy of ${TAG} would ship $(wc -l <<<"${shipped}") paths (.env.prod lands as ~/bss/.env):"
  grep -v '^web/assets/' <<<"${shipped}" | sed 's/^/    /'
  printf '    ... and %s files under web/assets/\n' "$(grep -c '^web/assets/.' <<<"${shipped}")"
  ! grep -q 'env\.smoke' <<<"${shipped}" || fail ".env.smoke would be shipped"
  ! grep -q '^SMOKE_BASIC_AUTH=' .env.prod || fail ".env.prod has a SMOKE_BASIC_AUTH line"
  log "Dry run OK: .env.smoke is not shipped, .env.prod has no SMOKE_BASIC_AUTH line; nothing touched the network"
  exit 0
fi

log "Opening one SSH connection to ${REMOTE} for the whole deploy"
open_master

log "Checking that the shared network from vps-infra exists on ${REMOTE}"
# Postgres and Caddy live in the vps-infra project, which also creates this network.
# Without it compose fails with a vaguer error, so check up front.
rssh "docker network inspect shared >/dev/null 2>&1" \
  || fail "network 'shared' not found on ${REMOTE} - deploy vps-infra first"

log "Building ${IMAGE_NAME}:${TAG} locally (runtime stage)"
docker build --target runtime -t "${IMAGE_NAME}:${TAG}" .

# Skip the ship if the server already has this exact image. Not by .Id: both sides use the
# containerd image store, where .Id is the manifest digest, and that differs between two
# builds of identical content - so after a rebuild .Id never matches what the server holds.
# Instead fingerprint what defines the image on both sides: the layer diff IDs plus the
# full Config (CMD/ENV/HEALTHCHECK changes add no layer). Prints nothing if the image is
# missing, so a missing image never matches.
FINGERPRINT='out=$(docker image inspect --format "{{json .RootFS.Layers}}{{json .Config}}" "$1" 2>/dev/null) && printf "%s\n" "$out" | sha256sum | cut -d" " -f1'
local_fp="$(sh -c "${FINGERPRINT}" _ "${IMAGE_NAME}:${TAG}")"
remote_fp="$(rssh "sh -c $(printf '%q' "${FINGERPRINT}") _ ${IMAGE_NAME}:${TAG}" || true)"
[[ -n "${local_fp}" ]] || fail "cannot fingerprint local image ${IMAGE_NAME}:${TAG}"
if [[ "${remote_fp}" == "${local_fp}" ]]; then
  log "image already on server, skipping ship (${IMAGE_NAME}:${TAG} fingerprint ${local_fp:0:12})"
else
  log "Shipping image to ${REMOTE} (gzip over ssh)"
  docker save "${IMAGE_NAME}:${TAG}" | gzip -1 | rssh 'gunzip | docker load'
fi

log "Syncing compose files"
rssh "mkdir -p ${REMOTE_DIR}"
ship_config "${REMOTE}:${REMOTE_DIR}"

# No --quiet-pull on any compose call below: the image only exists locally on the VPS
# (docker load, no registry), and with that flag compose hangs trying to check a
# repository that doesn't exist.

# Migrations run against the NEW image before the app serves any traffic: starting the
# app first would leave a window where new code talks to the old schema. The database
# itself is not in this stack (vps-infra runs it), so there is nothing to start first.
#
# An unreachable database or a wrong password does not fail fast: the client keeps
# retrying the connection, so without a deadline the deploy just looks slow. Killing the
# local ssh does not reliably stop the remote one-off container, so a timed-out run is
# removed explicitly and cannot finish later behind our back.
log "Running database migrations (timeout ${MIGRATE_TIMEOUT}s)"
migrate_rc=0
timeout "${MIGRATE_TIMEOUT}" ssh "${SSH_OPTS[@]}" "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} run --rm --no-deps app alembic upgrade head" \
  || migrate_rc=$?
if (( migrate_rc == 124 )); then
  # Matched by compose's own labels (this project + oneoff), so nothing else on the host
  # is touched. A fixed name would be simpler, but `compose run --name` hangs instead of
  # running.
  rssh "docker ps -aq --filter 'label=com.docker.compose.project=bss' --filter 'label=com.docker.compose.oneoff=True' | xargs -r docker rm -f >/dev/null 2>&1" || true
  fail "migrations did not finish within ${MIGRATE_TIMEOUT}s - the database is most likely unreachable.
  Check ${REMOTE_DIR}/.env on ${REMOTE} (POSTGRES_PASSWORD must be the bss role's password in vps-infra)
  and that the vps-infra postgres is running and attached to the 'shared' network:
    docker network inspect shared --format '{{range .Containers}}{{.Name}} {{end}}'"
elif (( migrate_rc != 0 )); then
  fail "database migration failed (see output above)"
fi

log "Starting the app"
rssh "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} up -d --remove-orphans"

# Both services must go healthy: the app (/healthz) and the billing worker (its liveness
# file, touched after its first wake-up). One unhealthy service ends the wait at once.
log "Waiting for the app, worker and mcp containers to become healthy"
deadline=$(( SECONDS + 90 ))
declare -A status=([app]="" [worker]="" [mcp]="")
while (( SECONDS < deadline )); do
  for svc in app worker mcp; do
    [[ "${status[${svc}]}" == "healthy" ]] && continue
    cid="$(rssh "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} ps -q ${svc}" || true)"
    if [[ -n "${cid}" ]]; then
      status[${svc}]="$(rssh "docker inspect -f '{{.State.Health.Status}}' ${cid}" 2>/dev/null || echo unknown)"
    fi
  done
  [[ "${status[app]}" == "healthy" && "${status[worker]}" == "healthy" && "${status[mcp]}" == "healthy" ]] && break
  [[ "${status[app]}" == "unhealthy" || "${status[worker]}" == "unhealthy" || "${status[mcp]}" == "unhealthy" ]] && break
  sleep 3
done

if [[ "${status[app]}" != "healthy" || "${status[worker]}" != "healthy" || "${status[mcp]}" != "healthy" ]]; then
  printf '\n--- remote logs (last 100 lines) ---\n' >&2
  rssh "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} logs --tail=100" >&2 || true
  fail "containers did not become healthy (last status: app ${status[app]:-none}, worker ${status[worker]:-none}, mcp ${status[mcp]:-none})"
fi

log "Remote smoke test"
rssh "curl -fsS --max-time 5 http://127.0.0.1:18081/healthz" \
  || { rssh "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} logs --tail=100" >&2; fail "smoke test failed"; }
printf '\n'

# The billing freshness signal answers with a known status. 503 is valid (no run yet, a
# stale or failed run) - the deploy only proves the endpoint and the worker's table work;
# freshness is the external monitor's job. The body is date, status and age only.
log "Remote smoke test: /billing-status"
billing="$(rssh "curl -sS --max-time 5 -w ' %{http_code}' http://127.0.0.1:18081/billing-status")" \
  || fail "cannot reach /billing-status on ${REMOTE}"
printf '    %s\n' "${billing}"
# 200 ok or running (a run in progress - e.g. the one this deploy's worker restart just
# took over), 503 stale/failed/running/none; shared with smoke.sh.
./scripts/billing-status-check.sh "${billing##* }" <<<"${billing% *}" \
  || fail "/billing-status answered with an unknown status: ${billing}"

# The UI goes live only after the app that serves its API is up. The shared Caddy
# bind-mounts ~/bss/web (vps-infra), and a bind mount holds the directory's inode: the
# directory is created once and only ever synced INTO, never replaced or renamed, or
# Caddy would keep serving the old one. Hashed assets first, then index.html (so the live
# index.html never names an asset that is not there yet), then a pass that deletes stale
# assets.
log "Shipping the admin UI into ~/${REMOTE_DIR}/web (synced into the directory, never replaced)"
rssh "mkdir -p ${REMOTE_DIR}/web"
ship_web "${REMOTE}:${REMOTE_DIR}"

log "Web smoke test through Caddy (${WEB_URL})"
SMOKE_BASIC_AUTH="${SMOKE_BASIC_AUTH}" BSS_SMOKE_SECRET="${BSS_SMOKE_SECRET}" \
  ./scripts/web-smoke.sh "${WEB_URL}" "${TAG}" \
  || fail "web smoke test failed (see above). The app itself is deployed and healthy; check the vps-infra Caddy routes and the files in ~/${REMOTE_DIR}/web"

log "MCP smoke test through Caddy (${MCP_URL})"
./scripts/mcp-smoke.sh "${MCP_URL}" caddy \
  || fail "MCP smoke test failed (see above). App, UI and mcp are deployed and healthy; check the vps-infra Caddy carve-outs (docs/vps-infra-change-request.md)"

log "Pruning old ${IMAGE_NAME} images on ${REMOTE} (keeping ${KEEP_IMAGES}, nothing else is touched)"
rssh "docker images '${IMAGE_NAME}' --format '{{.ID}} {{.CreatedAt}}' \
  | sort -k2 -r | tail -n +$((KEEP_IMAGES + 1)) | awk '{print \$1}' \
  | xargs -r docker rmi" || true

log "Deployed ${IMAGE_NAME}:${TAG} — app at 127.0.0.1:18081 on ${REMOTE}, UI at ${WEB_URL}"
