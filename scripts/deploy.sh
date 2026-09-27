#!/usr/bin/env bash
# Build the image in WSL, ship it to the VPS, restart the stack there, wait until it is
# healthy. This is the ONLY script allowed to mutate state on the VPS.
#
# The VPS has 1 vCPU and runs a production VPN server, so nothing is ever built remotely.
set -euo pipefail

REMOTE="${REMOTE:-vps-test}"
REMOTE_DIR="${REMOTE_DIR:-lab}"
IMAGE_NAME="${IMAGE_NAME:-__PROJECT__-app}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
KEEP_IMAGES=3

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -f .env.prod ]] || fail ".env.prod is missing (copy .env.example and fill it in)"

log "Building ${IMAGE_NAME}:${TAG} locally (runtime stage)"
docker build --target runtime -t "${IMAGE_NAME}:${TAG}" .

log "Shipping image to ${REMOTE} (gzip over ssh)"
docker save "${IMAGE_NAME}:${TAG}" | gzip -1 | ssh "${REMOTE}" 'gunzip | docker load'

log "Syncing compose files"
ssh "${REMOTE}" "mkdir -p ${REMOTE_DIR}"

# WITH_CADDY selects whether this deployment owns a Caddy container at all - see
# .env.prod / README.md. Read straight from the file rather than sourcing it, so the rest
# of .env.prod's variables never leak into this script's own environment.
WITH_CADDY="$(grep -E '^WITH_CADDY=' .env.prod 2>/dev/null | cut -d= -f2- || true)"
WITH_CADDY="${WITH_CADDY:-false}"
CADDY_PROFILE=()
[[ "${WITH_CADDY}" == "true" ]] && CADDY_PROFILE=(--profile caddy)

caddy_changed=false
if [[ "${WITH_CADDY}" == "true" ]]; then
  # up -d only diffs container config (image, env, volume list), never the *contents* of a
  # bind-mounted file - so a Caddyfile edit needs an explicit, scoped recreate of caddy to
  # actually take effect. Hash what's on the VPS now, before rsync overwrites it, so we know
  # after the stack is (re)started whether that recreate is actually needed.
  local_caddy_hash="$(sha256sum Caddyfile | awk '{print $1}')"
  remote_caddy_hash="$(ssh "${REMOTE}" "sha256sum ${REMOTE_DIR}/Caddyfile 2>/dev/null | awk '{print \$1}'")"
  [[ "${local_caddy_hash}" != "${remote_caddy_hash}" ]] && caddy_changed=true
  rsync -az compose.yaml compose.prod.yaml Caddyfile scripts/cron-backup.sh "${REMOTE}:${REMOTE_DIR}/"
else
  rsync -az compose.yaml compose.prod.yaml scripts/cron-backup.sh "${REMOTE}:${REMOTE_DIR}/"
fi
ssh "${REMOTE}" "chmod +x ${REMOTE_DIR}/cron-backup.sh"
# compose auto-loads a file named .env from its project directory, so EVERY remote
# compose call sees the secrets - not just the one that starts the stack
rsync -az .env.prod "${REMOTE}:${REMOTE_DIR}/.env"

log "Starting database only"
ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} up -d --quiet-pull --wait db" \
  || fail "database did not start"

# Migrations run against the NEW image before the app serves any traffic: starting the
# app first would leave a window where new code talks to the old schema.
log "Running database migrations"
ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} run --rm --no-deps app alembic upgrade head" \
  || fail "database migration failed (see output above)"

log "Starting the rest of the stack"
ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} ${CADDY_PROFILE[*]} up -d --quiet-pull --remove-orphans"

if [[ "${WITH_CADDY}" == "true" ]]; then
  if [[ "${caddy_changed}" == true ]]; then
    log "Caddyfile changed — recreating the caddy container to pick it up"
    ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
      IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} ${CADDY_PROFILE[*]} up -d --force-recreate --no-deps caddy" \
      || fail "failed to recreate caddy after a config change"
  else
    log "Caddyfile unchanged — leaving caddy running as-is"
  fi
fi

log "Waiting for the app container to become healthy"
deadline=$(( SECONDS + 90 ))
status=""
while (( SECONDS < deadline )); do
  cid="$(ssh "${REMOTE}" "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} ps -q app" || true)"
  if [[ -n "${cid}" ]]; then
    status="$(ssh "${REMOTE}" "docker inspect -f '{{.State.Health.Status}}' ${cid}" 2>/dev/null || echo unknown)"
    [[ "${status}" == "healthy" ]] && break
    [[ "${status}" == "unhealthy" ]] && break
  fi
  sleep 3
done

if [[ "${status}" != "healthy" ]]; then
  printf '\n--- remote logs (last 100 lines) ---\n' >&2
  ssh "${REMOTE}" "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} logs --tail=100" >&2 || true
  fail "container did not become healthy (last status: ${status:-none})"
fi

log "Remote smoke test"
ssh "${REMOTE}" "curl -fsS --max-time 5 http://127.0.0.1:__APP_PORT__/healthz" \
  || { ssh "${REMOTE}" "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} logs --tail=100" >&2; fail "smoke test failed"; }
printf '\n'

log "Pruning old ${IMAGE_NAME} images on ${REMOTE} (keeping ${KEEP_IMAGES}, nothing else is touched)"
ssh "${REMOTE}" "docker images '${IMAGE_NAME}' --format '{{.ID}} {{.CreatedAt}}' \
  | sort -k2 -r | tail -n +$((KEEP_IMAGES + 1)) | awk '{print \$1}' \
  | xargs -r docker rmi" || true

log "Deployed ${IMAGE_NAME}:${TAG} — reachable at 127.0.0.1:__APP_PORT__ on ${REMOTE}"
