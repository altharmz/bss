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

[[ -f .env.prod ]] || fail ".env.prod is missing (copy .env.example and fill it in)"

log "Checking that the shared network from vps-infra exists on ${REMOTE}"
# Postgres and Caddy live in the vps-infra project, which also creates this network.
# Without it compose fails with a vaguer error, so check up front.
ssh "${REMOTE}" "docker network inspect shared >/dev/null 2>&1" \
  || fail "network 'shared' not found on ${REMOTE} - deploy vps-infra first"

log "Building ${IMAGE_NAME}:${TAG} locally (runtime stage)"
docker build --target runtime -t "${IMAGE_NAME}:${TAG}" .

log "Shipping image to ${REMOTE} (gzip over ssh)"
docker save "${IMAGE_NAME}:${TAG}" | gzip -1 | ssh "${REMOTE}" 'gunzip | docker load'

log "Syncing compose files"
ssh "${REMOTE}" "mkdir -p ${REMOTE_DIR}"
rsync -az compose.yaml compose.prod.yaml "${REMOTE}:${REMOTE_DIR}/"
# compose auto-loads a file named .env from its project directory, so EVERY remote
# compose call sees the secrets - not just the one that starts the stack
rsync -az .env.prod "${REMOTE}:${REMOTE_DIR}/.env"

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
timeout "${MIGRATE_TIMEOUT}" ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} run --rm --no-deps app alembic upgrade head" \
  || migrate_rc=$?
if (( migrate_rc == 124 )); then
  # Matched by compose's own labels (this project + oneoff), so nothing else on the host
  # is touched. A fixed name would be simpler, but `compose run --name` hangs instead of
  # running.
  ssh "${REMOTE}" "docker ps -aq --filter 'label=com.docker.compose.project=bss' --filter 'label=com.docker.compose.oneoff=True' | xargs -r docker rm -f >/dev/null 2>&1" || true
  fail "migrations did not finish within ${MIGRATE_TIMEOUT}s - the database is most likely unreachable.
  Check ${REMOTE_DIR}/.env on ${REMOTE} (POSTGRES_PASSWORD must be the bss role's password in vps-infra)
  and that the vps-infra postgres is running and attached to the 'shared' network:
    docker network inspect shared --format '{{range .Containers}}{{.Name}} {{end}}'"
elif (( migrate_rc != 0 )); then
  fail "database migration failed (see output above)"
fi

log "Starting the app"
ssh "${REMOTE}" "cd ${REMOTE_DIR} && \
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} up -d --remove-orphans"

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
ssh "${REMOTE}" "curl -fsS --max-time 5 http://127.0.0.1:18081/healthz" \
  || { ssh "${REMOTE}" "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} logs --tail=100" >&2; fail "smoke test failed"; }
printf '\n'

log "Pruning old ${IMAGE_NAME} images on ${REMOTE} (keeping ${KEEP_IMAGES}, nothing else is touched)"
ssh "${REMOTE}" "docker images '${IMAGE_NAME}' --format '{{.ID}} {{.CreatedAt}}' \
  | sort -k2 -r | tail -n +$((KEEP_IMAGES + 1)) | awk '{print \$1}' \
  | xargs -r docker rmi" || true

log "Deployed ${IMAGE_NAME}:${TAG} — reachable at 127.0.0.1:18081 on ${REMOTE}"
