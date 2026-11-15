#!/usr/bin/env bash
# Build the image in WSL, ship it to the VPS, restart the stack there, wait until it is
# healthy. This is the ONLY script allowed to mutate state on the VPS.
#   deploy.sh              deploy HEAD; a failure after the new release starts rolls back
#   deploy.sh --rollback   go back to the previous release (DRY_RUN=1: print the plan)
#
# The VPS has 1 vCPU and runs a production VPN server, so nothing is ever built remotely.
#
# Releases on the server (docs/operations.md "Rollback"): ~/bss/releases/<tag>/ holds the
# compose files, the UI (web/) and release.env (tag, commit, alembic_rev, deployed_at) of
# each kept release; ~/bss/CURRENT and ~/bss/PREVIOUS name one tag each.
set -euo pipefail

MODE=deploy
case "${1:-}" in
  "") ;;
  --rollback) MODE=rollback ;;
  *) printf 'usage: %s [--rollback]\n' "$0" >&2; exit 2 ;;
esac

REMOTE="${REMOTE:-vps-test}"
# Relative to the deploy user's home, i.e. ~/bss - one directory per project, so
# two apps from this template on the same VPS never overwrite each other's files.
REMOTE_DIR="${REMOTE_DIR:-bss}"
IMAGE_NAME="${IMAGE_NAME:-bss-app}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
DEPLOY_COMMIT="$(git rev-parse HEAD)"
COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
KEEP_IMAGES=3
MIGRATE_TIMEOUT=120

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# The point of no return of a deploy is `compose up -d` of the new release: ROLLBACK_ARMED
# is set right before it, and any fail() after it rolls back to the release that served
# before (once - it is cleared first, so a failure during the rollback just stops). Before
# it the old containers still serve and the schema is unchanged (alembic runs all pending
# migrations in one transaction), which SERVER_NOTE says.
ROLLBACK_ARMED=0
FAILURE=""
SERVER_NOTE=""
ROLLED_BACK_TO=""
ROLLED_BACK_COMMIT=""
fail() {
  printf '\033[1;31mFAILED:\033[0m %s\n' "$*" >&2
  if (( ROLLBACK_ARMED )); then
    ROLLBACK_ARMED=0
    FAILURE="$*"
    log "The new release is running and failed: rolling back to the previous release"
    rollback_to_previous auto
    printf '\033[1;31mDEPLOY FAILED, ROLLED BACK to %s (%s); the failure was: %s\033[0m\n' \
      "${ROLLED_BACK_TO}" "${ROLLED_BACK_COMMIT}" "${FAILURE}" >&2
  elif [[ -n "${FAILURE}" ]]; then
    printf '    This failure happened while rolling back after: %s\n' "${FAILURE}" >&2
  elif [[ -n "${SERVER_NOTE}" ]]; then
    printf '    %s\n' "${SERVER_NOTE}" >&2
  fi
  exit 1
}

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

# Rollback drills (docs/operations.md "Rollback"): inert unless set, for the live drill the
# human approves. BSS_DEPLOY_DRILL makes a deploy fail at one chosen point (or a cleanup
# step warn); BSS_ROLLBACK_DRILL makes a manual rollback's schema downgrade overrun.
DEPLOY_DRILL="${BSS_DEPLOY_DRILL:-}"
ROLLBACK_DRILL="${BSS_ROLLBACK_DRILL:-}"
# `compose run -e` for the migration one-off (alembic/env.py), `up` environment for the app
MIGRATE_DRILL_ENV=""
DOWNGRADE_DRILL_ENV=""
UP_DRILL_ENV=""
FAIL_POINTS="health deployed healthz billing-status web-sync web-smoke mcp-smoke oauth-smoke"
drill_banner() { printf '\033[1;33m!!! %s (docs/operations.md "Rollback")\033[0m\n' "$*"; }
if [[ -n "${DEPLOY_DRILL}" ]]; then
  [[ "${MODE}" == deploy ]] || fail "BSS_DEPLOY_DRILL applies to a deploy, not to --rollback"
  case "${DEPLOY_DRILL}" in
    fail-migration)
      MIGRATE_DRILL_ENV="-e BSS_MIGRATION_DRILL=fail"
      drill_banner "BSS_DEPLOY_DRILL=fail-migration: the migrations of this deploy WILL FAIL inside their transaction; nothing may change on the server" ;;
    slow-migration)
      MIGRATE_DRILL_ENV="-e BSS_MIGRATION_DRILL=slow"
      drill_banner "BSS_DEPLOY_DRILL=slow-migration: the migrations of this deploy WILL OVERRUN the ${MIGRATE_TIMEOUT}s deadline; nothing may change on the server" ;;
    unhealthy)
      UP_DRILL_ENV="BSS_HEALTH_DRILL=unhealthy"
      drill_banner "BSS_DEPLOY_DRILL=unhealthy: the new release's /healthz WILL answer 503, the health gate fails and the deploy rolls back" ;;
    fail-after-*)
      [[ " ${FAIL_POINTS} " == *" ${DEPLOY_DRILL#fail-after-} "* ]] \
        || fail "unknown BSS_DEPLOY_DRILL=${DEPLOY_DRILL} (fail-after-<point>, points: ${FAIL_POINTS})"
      drill_banner "BSS_DEPLOY_DRILL=${DEPLOY_DRILL}: this deploy WILL FAIL right after that step and roll back" ;;
    warn-prune)
      drill_banner "BSS_DEPLOY_DRILL=warn-prune: the cleanup steps WILL FAIL; they only warn, the deploy must still pass" ;;
    *) fail "unknown BSS_DEPLOY_DRILL=${DEPLOY_DRILL} (fail-migration, slow-migration, unhealthy, fail-after-<point>, warn-prune)" ;;
  esac
fi
if [[ -n "${ROLLBACK_DRILL}" ]]; then
  [[ "${MODE}" == rollback ]] || fail "BSS_ROLLBACK_DRILL applies to --rollback, not to a deploy"
  [[ "${ROLLBACK_DRILL}" == slow-downgrade ]] \
    || fail "unknown BSS_ROLLBACK_DRILL=${ROLLBACK_DRILL} (the only value is slow-downgrade)"
  DOWNGRADE_DRILL_ENV="-e BSS_MIGRATION_DRILL=slow"
  drill_banner "BSS_ROLLBACK_DRILL=slow-downgrade: the schema downgrade WILL OVERRUN the ${MIGRATE_TIMEOUT}s deadline; the rollback must refuse and leave the current release running"
fi

# A deploy drill's failure point: fails only while the deploy is armed, so the smokes of a
# rollback (which reuse the same functions) never trip it.
drill_point() { # <point> <what just passed>
  (( ROLLBACK_ARMED )) && [[ "${DEPLOY_DRILL}" == "fail-after-$1" ]] || return 0
  fail "drill: forced failure after $2 (BSS_DEPLOY_DRILL=fail-after-$1)"
}
# warn-prune: each cleanup step fails before doing anything (its caller only warns).
drill_warn() {
  [[ "${DEPLOY_DRILL}" == warn-prune ]] || return 0
  printf '    drill: forcing this cleanup step to fail (BSS_DEPLOY_DRILL=warn-prune)\n'
  return 1
}

# A rollback ships nothing, so it needs neither .env.prod nor a clean tree.
if [[ "${MODE}" == deploy ]]; then
  [[ -f .env.prod ]] || fail ".env.prod is missing (copy .env.example and fill it in)"

  # TAG is the commit hash. A dirty tree would ship different content (image or compose
  # files) under a clean-looking tag, so refuse before touching the network.
  dirty="$(git status --porcelain)" || fail "not a git work tree - cannot tell what would be deployed"
  [[ -z "${dirty}" ]] || fail "working tree is not clean - ${IMAGE_NAME}:${TAG} would not describe what ships.
Commit or stash first:
${dirty}"
fi

# The web smoke test goes through the shared Caddy with the app's read-only smoke secret.
# Read only that one variable; it is handed to the smoke script in its environment, never
# printed.
#
# The secret lives in .env.smoke - local only: gitignored, never rsynced (the
# workstation-only counterpart of .env.prod, which IS shipped to the VPS as ~/bss/.env).
# SMOKE_ENV points elsewhere only for a rehearsal with a dummy file.
WEB_URL="${WEB_URL:-https://vpn2811.duckdns.org:8445}"
# The MCP connector's public URL: MCP_PUBLIC_URL in compose.prod.yaml (port 443 through the
# host's SNI router to Caddy's :8445). Its OAuth metadata names this URL only.
MCP_URL="${MCP_URL:-https://bss2811.duckdns.org}"
SMOKE_ENV="${SMOKE_ENV:-.env.smoke}"
[[ -f "${SMOKE_ENV}" ]] \
  || fail "${SMOKE_ENV} is missing: create .env.smoke with BSS_SMOKE_SECRET=<hex> (see .env.example)"
# The app's read-only smoke credential (docs/decisions/users.md): the app gets it from
# .env.prod, the web smoke from .env.smoke - the same value; a mismatch fails the smoke.
BSS_SMOKE_SECRET="$(sed -n 's/^BSS_SMOKE_SECRET=//p' "${SMOKE_ENV}" | tail -n 1)"
[[ -n "${BSS_SMOKE_SECRET}" ]] \
  || fail "BSS_SMOKE_SECRET is missing in ${SMOKE_ENV} (the same value as in .env.prod; see .env.example)"
# Optional: the production `mcp` smoke user for the OAuth smoke (created by the human,
# docs/mcp.md). Without both values that step says it was skipped; read only these two
# lines, handed to the script in its environment, never printed.
MCP_SMOKE_USER="$(sed -n 's/^MCP_SMOKE_USER=//p' "${SMOKE_ENV}" | tail -n 1)"
MCP_SMOKE_PASSWORD="$(sed -n 's/^MCP_SMOKE_PASSWORD=//p' "${SMOKE_ENV}" | tail -n 1)"
if [[ "${MODE}" == deploy ]]; then
  grep -q '^BSS_SERVICE_SECRET=.' .env.prod \
    || fail "BSS_SERVICE_SECRET is empty or missing in .env.prod (see .env.example)"
  grep -q '^BSS_SMOKE_SECRET=.' .env.prod \
    || fail "BSS_SMOKE_SECRET is empty or missing in .env.prod (see .env.example)"
fi

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
# The release's own copy: its compose files and the exact UI files, into
# ~/bss/releases/<tag>/. Nothing serves from there; ~/bss/web is synced from it later.
# --list <prefix>: send nothing, print each path that would ship under that prefix.
ship_release() { # <destination> [--list <prefix>]
  local dest="$1" files=() web=()
  if [[ "${2:-}" == --list ]]; then
    files=(--dry-run "--out-format=${3}%n")
    web=(--dry-run "--out-format=${3}web/%n")
  fi
  "${RSYNC[@]}" "${files[@]}" compose.yaml compose.prod.yaml "${dest}/" &&
    "${RSYNC[@]}" "${web[@]}" --delete frontend/dist/ "${dest}/web/"
}

# compose on the VPS for one release: rcompose <tag> <compose arguments...>
rcompose() {
  local tag="$1"
  shift
  rssh "cd ${REMOTE_DIR} && IMAGE_NAME=${IMAGE_NAME} TAG=${tag} ${COMPOSE[*]} $*"
}
remote_logs() { # <tag>
  printf '\n--- remote logs (last 100 lines) ---\n' >&2
  rcompose "$1" logs --tail=100 >&2 || true
}

# Start a stored release: its compose files back into ~/bss, then up. The optional
# environment (VAR=value) reaches compose's interpolation - only the unhealthy drill uses it.
activate_release() { # <tag> [environment]
  rssh "cd ${REMOTE_DIR} && cp releases/$1/compose.yaml releases/$1/compose.prod.yaml ." \
    || fail "cannot copy the compose files of ~/${REMOTE_DIR}/releases/$1"
  rssh "cd ${REMOTE_DIR} && ${2:+$2 }IMAGE_NAME=${IMAGE_NAME} TAG=$1 ${COMPOSE[*]} up -d --remove-orphans" \
    || fail "compose up -d of ${IMAGE_NAME}:$1 failed"
}

# All three must go healthy: the app (/healthz), the billing worker (its liveness file,
# touched after its first wake-up) and mcp. One unhealthy service ends the wait at once.
# 240 s = the longest start period in compose.prod.yaml (app, 180 s) + one 60 s probe
# interval: a container ready just after its start period is seen only at the next probe.
# Returns 1 (after printing the remote logs) unless all three are healthy; HEALTH_STATUS
# names the last status of each.
HEALTH_STATUS=""
wait_healthy() { # <tag>
  local tag="$1" deadline svc cid
  local -A status=([app]="" [worker]="" [mcp]="")
  deadline=$(( SECONDS + 240 ))
  while (( SECONDS < deadline )); do
    for svc in app worker mcp; do
      [[ "${status[${svc}]}" == "healthy" ]] && continue
      cid="$(rcompose "${tag}" ps -q "${svc}" || true)"
      if [[ -n "${cid}" ]]; then
        status[${svc}]="$(rssh "docker inspect -f '{{.State.Health.Status}}' ${cid}" 2>/dev/null || echo unknown)"
      fi
    done
    [[ "${status[app]}" == "healthy" && "${status[worker]}" == "healthy" && "${status[mcp]}" == "healthy" ]] && break
    [[ "${status[app]}" == "unhealthy" || "${status[worker]}" == "unhealthy" || "${status[mcp]}" == "unhealthy" ]] && break
    sleep 3
  done
  HEALTH_STATUS="last status: app ${status[app]:-none}, worker ${status[worker]:-none}, mcp ${status[mcp]:-none}"
  if [[ "${status[app]}" != "healthy" || "${status[worker]}" != "healthy" || "${status[mcp]}" != "healthy" ]]; then
    remote_logs "${tag}"
    return 1
  fi
}

record_deployed() { # <commit>
  rssh "cd ${REMOTE_DIR} && printf '%s\\n' '$1' > DEPLOYED.tmp && mv DEPLOYED.tmp DEPLOYED" \
    || fail "cannot write ~/${REMOTE_DIR}/DEPLOYED"
}

check_deployed() { # <commit> <tag>
  local deployed_commit
  log "Remote smoke test: deployed commit"
  deployed_commit="$(rssh "cat ${REMOTE_DIR}/DEPLOYED")" || deployed_commit=""
  [[ "${deployed_commit}" == "$1" ]] \
    || fail "deployed commit record does not match $1"
  printf '    Deployed commit: %s (image %s:%s)\n' "${deployed_commit}" "${IMAGE_NAME}" "$2"
  drill_point deployed "the deployed-commit check"
}

# /healthz and /billing-status on the VPS itself.
smoke_backend() { # <tag>
  local billing
  log "Remote smoke test"
  rssh "curl -fsS --max-time 5 http://127.0.0.1:18081/healthz" \
    || { remote_logs "$1"; fail "smoke test failed"; }
  printf '\n'
  drill_point healthz "/healthz"

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
  drill_point billing-status "/billing-status"
}

# Through Caddy: the UI of this build, the MCP connector and (unless --no-oauth) its login.
smoke_edge() { # <tag> [--no-oauth]
  log "Web smoke test through Caddy (${WEB_URL})"
  BSS_SMOKE_SECRET="${BSS_SMOKE_SECRET}" \
    ./scripts/web-smoke.sh "${WEB_URL}" "$1" \
    || fail "web smoke test failed (see above). The app itself is deployed and healthy; check the vps-infra Caddy routes and the files in ~/${REMOTE_DIR}/web"
  drill_point web-smoke "the web smoke"

  log "MCP smoke test through Caddy (${MCP_URL})"
  ./scripts/mcp-smoke.sh "${MCP_URL}" caddy \
    || fail "MCP smoke test failed (see above). App, UI and mcp are deployed and healthy; check the vps-infra Caddy carve-outs (docs/decisions/infra.md)"
  drill_point mcp-smoke "the MCP smoke"

  [[ "${2:-}" == --no-oauth ]] && return 0
  log "MCP OAuth login smoke through Caddy (${MCP_URL})"
  MCP_SMOKE_USER="${MCP_SMOKE_USER}" MCP_SMOKE_PASSWORD="${MCP_SMOKE_PASSWORD}" \
    python3 scripts/mcp-oauth-smoke.py "${MCP_URL}" caddy \
    || fail "MCP OAuth smoke failed (see above). Everything is deployed; is the smoke user in ${SMOKE_ENV} an active mcp user with that password (docs/mcp.md)?"
  drill_point oauth-smoke "the MCP OAuth smoke"
}

# Every smoke of a running release, in deploy order (the UI must already be in ~/bss/web).
run_smokes() { # <tag> <commit> [--no-oauth]
  check_deployed "$2" "$1"
  smoke_backend "$1"
  smoke_edge "$1" "${3:-}"
}

# The workstation's copy of the record (gitignored; `make deployed` shows it). Only this
# script writes it: agents never record a deployment by hand (docs/decisions/harness.md).
# Written when the VPS runs the new backend, with checks=running; the end of the script
# sets checks=passed. checks=running afterwards means a later smoke failed.
DEPLOYED_FILE=.deployed
# A rollback writes checks=rolled-back with the previous release's commit and tag and
# rolled_back_from=<the failed tag>.
write_local_record() { # <checks> [<commit> <tag> <rolled back from>]
  local commit="${2:-${DEPLOY_COMMIT}}" tag="${3:-${TAG}}"
  {
    printf 'commit=%s\n' "${commit}"
    printf 'tag=%s\n' "${tag}"
    printf 'subject=%s\n' "$(git log -1 --format=%s "${commit}" 2>/dev/null)"
    printf 'migration=%s\n' "$(git ls-tree --name-only "${commit}" alembic/versions/ 2>/dev/null | grep -E '/[0-9]+_[^/]*\.py$' | sort | tail -n 1 | xargs -r basename)"
    printf 'deployed_at=%s\n' "${DEPLOYED_AT}"
    printf 'checks=%s\n' "$1"
    [[ -z "${4:-}" ]] || printf 'rolled_back_from=%s\n' "$4"
  } > "${DEPLOYED_FILE}.tmp" && mv "${DEPLOYED_FILE}.tmp" "${DEPLOYED_FILE}"
}

# A file under ~/bss on the VPS; empty when it does not exist.
remote_cat() { # <path below ~/bss>
  rssh "cat ${REMOTE_DIR}/$1 2>/dev/null" || true
}
field() { # <name> <key=value lines>
  sed -n "s/^$1=//p" <<<"$2" | tail -n 1
}
is_tag() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }

# The alembic revision a commit migrates to: `revision` of the highest-numbered file in
# alembic/versions/ (the file prefix, e.g. revision = "0063").
alembic_rev_of() { # <commit>
  local file
  file="$(git ls-tree --name-only "$1" alembic/versions/ | grep -E '/[0-9]+_[^/]*\.py$' | sort | tail -n 1)"
  [[ -n "${file}" ]] || return 0
  git show "$1:${file}" | sed -nE 's/^revision(: *str)? *= *"([^"]+)".*/\2/p' | head -n 1
}

# ~/bss/web is what Caddy serves (a bind mount holds the directory's inode: it is only
# ever synced INTO, never replaced or renamed, or Caddy would keep serving the old one).
# Synced on the server from a stored release, in ship order: hashed assets first, then
# index.html (so the live index.html never names an asset that is not there yet), then a
# pass that deletes stale assets. Local paths on the VPS: no data crosses SSH.
sync_web() { # <tag>
  local src="releases/$1/web"
  rssh "cd ${REMOTE_DIR} && mkdir -p web" &&
    rssh "cd ${REMOTE_DIR} && rsync -a --exclude index.html ${src}/ web/" &&
    rssh "cd ${REMOTE_DIR} && rsync -a ${src}/index.html web/index.html" &&
    rssh "cd ${REMOTE_DIR} && rsync -a --delete ${src}/ web/" \
    || fail "syncing ~/${REMOTE_DIR}/${src} into ~/${REMOTE_DIR}/web failed"
}

# ~/bss/releases/<tag>/: compose files and UI from this checkout, then release.env last
# (a directory without release.env is never a rollback target).
record_release() { # <tag> <commit> <alembic rev>
  log "Storing the release in ~/${REMOTE_DIR}/releases/$1 (compose files, UI, release.env)"
  rssh "mkdir -p ${REMOTE_DIR}/releases/$1/web" || fail "cannot create ~/${REMOTE_DIR}/releases/$1"
  ship_release "${REMOTE}:${REMOTE_DIR}/releases/$1" \
    || fail "cannot ship the release files to ~/${REMOTE_DIR}/releases/$1"
  rssh "cd ${REMOTE_DIR}/releases/$1 && printf 'tag=$1\\ncommit=$2\\nalembic_rev=$3\\ndeployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)\\n' > release.env.tmp && mv release.env.tmp release.env" \
    || fail "cannot write ~/${REMOTE_DIR}/releases/$1/release.env"
}

write_current() { # <tag>
  rssh "cd ${REMOTE_DIR} && printf '%s\\n' '$1' > CURRENT.tmp && mv CURRENT.tmp CURRENT" \
    || fail "cannot write ~/${REMOTE_DIR}/CURRENT"
}

# The first deploy after releases existed: record what runs as a release, so this deploy
# can roll back to it. Runs before ship_config overwrites the compose files in ~/bss.
bootstrap_release() {
  local cid image running commit rev
  [[ -z "$(remote_cat CURRENT)" ]] || return 0
  cid="$(rssh "docker ps -q --filter label=com.docker.compose.project=bss --filter label=com.docker.compose.service=app" | head -n 1 || true)"
  if [[ -z "${cid}" ]]; then
    log "No release is running and ~/${REMOTE_DIR}/CURRENT does not exist: first deploy, no rollback possible for it"
    return 0
  fi
  image="$(rssh "docker inspect -f '{{.Config.Image}}' ${cid}")" \
    || fail "bootstrap: cannot inspect the running app container ${cid}"
  running="${image##*:}"
  is_tag "${running}" || fail "bootstrap: cannot tell the tag of the running image '${image}'"
  commit="$(remote_cat DEPLOYED)"
  rev="$(rcompose "${running}" exec -T app alembic current 2>/dev/null | awk 'NR == 1 { print $1 }' || true)"
  [[ -n "${rev}" ]] \
    || fail "bootstrap: cannot read the running schema revision (alembic current in ${IMAGE_NAME}:${running})"
  log "Bootstrapping: recording the running ${IMAGE_NAME}:${running} (alembic ${rev}) as ~/${REMOTE_DIR}/releases/${running} and CURRENT"
  rssh "cd ${REMOTE_DIR} && mkdir -p releases/${running} && cp compose.yaml compose.prod.yaml releases/${running}/ \
    && if [ -d web ]; then rsync -a --delete web/ releases/${running}/web/; else mkdir -p releases/${running}/web; fi \
    && printf 'tag=${running}\\ncommit=${commit:-unknown}\\nalembic_rev=${rev}\\ndeployed_at=unknown\\n' > releases/${running}/release.env.tmp \
    && mv releases/${running}/release.env.tmp releases/${running}/release.env" \
    || fail "bootstrap: cannot store the running release in ~/${REMOTE_DIR}/releases/${running}"
  write_current "${running}"
}

# Keep the KEEP_IMAGES newest bss-app images and always CURRENT and PREVIOUS; nothing else
# on the host is touched. Removed by reference (repo:tag), dangling ones by ID.
prune_images() { # <current> [<previous>]
  local listing victims
  listing="$(rssh "docker images '${IMAGE_NAME}' --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedAt}}'")" || return 1
  victims="$(sort -k3 -r <<<"${listing}" | tail -n +$((KEEP_IMAGES + 1)) \
    | awk -v c="${IMAGE_NAME}:$1" -v p="${IMAGE_NAME}:${2:-}" \
      'NF >= 3 && $1 != c && $1 != p { print ($1 ~ /:<none>$/ ? $2 : $1) }' | tr '\n' ' ')"
  [[ -z "${victims// /}" ]] || rssh "docker rmi ${victims}"
}

# Delete every ~/bss/releases/* directory except CURRENT's and PREVIOUS's.
prune_releases() { # <current> [<previous>]
  local dirs dir victims=""
  dirs="$(rssh "ls ${REMOTE_DIR}/releases")" || return 1
  for dir in ${dirs}; do
    [[ "${dir}" == "$1" || "${dir}" == "${2:-}" ]] && continue
    is_tag "${dir}" || continue
    victims+=" ${REMOTE_DIR}/releases/${dir}"
  done
  [[ -z "${victims}" ]] || rssh "rm -rf --${victims}"
}

# A timed-out one-off (migration) container: matched by compose's own labels (this project
# + oneoff), so nothing else on the host is touched. A fixed name would be simpler, but
# `compose run --name` hangs instead of running.
remove_oneoffs() {
  rssh "docker ps -aq --filter 'label=com.docker.compose.project=bss' --filter 'label=com.docker.compose.oneoff=True' | xargs -r docker rm -f >/dev/null 2>&1" || true
}

# Back one release: `auto` after a failed deploy of TAG (to the release that served before
# it), `manual` for make rollback (from CURRENT to PREVIOUS). Only one step back exists:
# afterwards CURRENT is the restored release and PREVIOUS is gone. A schema downgrade runs
# in the FAILED release's image (only it has the downgrade scripts) and is never forced.
rollback_to_previous() { # auto|manual
  local mode="$1" current previous failed target failed_env target_env
  local failed_rev target_rev failed_commit target_commit downgrade=no rc
  current="$(remote_cat CURRENT)"
  previous="$(remote_cat PREVIOUS)"
  if [[ "${mode}" == auto ]]; then
    # The pointers move only after a deploy's last smoke, so CURRENT still names the
    # release that served before - unless this was a re-deploy of CURRENT itself.
    failed="${TAG}"
    target="${current}"
    [[ "${target}" != "${failed}" ]] || target="${previous}"
  else
    failed="${current}"
    target="${previous}"
  fi
  if [[ -z "${failed}" || -z "${target}" ]] || ! is_tag "${target}" \
    || ! rssh "test -f ${REMOTE_DIR}/releases/${target}/release.env" \
    || ! rssh "docker image inspect ${IMAGE_NAME}:${target} >/dev/null 2>&1"; then
    [[ "${mode}" == manual ]] || remote_logs "${failed}"
    fail "cannot roll back: no previous release on the server - deploy instead (previous: '${target}'; its ~/${REMOTE_DIR}/releases entry or image is missing)"
  fi
  failed_env="$(remote_cat "releases/${failed}/release.env")"
  target_env="$(remote_cat "releases/${target}/release.env")"
  failed_rev="$(field alembic_rev "${failed_env}")"
  target_rev="$(field alembic_rev "${target_env}")"
  failed_commit="$(field commit "${failed_env}")"
  target_commit="$(field commit "${target_env}")"
  [[ -n "${failed_rev}" && -n "${target_rev}" ]] \
    || fail "cannot roll back: the alembic revision of ${failed} ('${failed_rev}') or ${target} ('${target_rev}') is unknown (~/${REMOTE_DIR}/releases/*/release.env)"
  [[ "${failed_rev}" == "${target_rev}" ]] || downgrade=yes

  if [[ "${DRY_RUN:-}" == 1 ]]; then
    log "Rollback plan (dry run: nothing is changed)"
    printf '    current:  %s (commit %s, alembic %s)\n' "${failed}" "${failed_commit:-unknown}" "${failed_rev}"
    printf '    previous: %s (commit %s, alembic %s)\n' "${target}" "${target_commit:-unknown}" "${target_rev}"
    if [[ "${downgrade}" == yes ]]; then
      printf '    schema downgrade: yes (alembic downgrade %s in %s:%s; refused if it would lose data)\n' \
        "${target_rev}" "${IMAGE_NAME}" "${failed}"
    else
      printf '    schema downgrade: no\n'
    fi
    printf '    compose files: ~/%s/releases/%s/compose.yaml, compose.prod.yaml -> ~/%s/\n' \
      "${REMOTE_DIR}" "${target}" "${REMOTE_DIR}"
    printf '    UI files that would change in ~/%s/web:\n' "${REMOTE_DIR}"
    rssh "cd ${REMOTE_DIR} && rsync -a --dry-run --delete --out-format='%n' releases/${target}/web/ web/" \
      | sed 's/^/      /' || true
    exit 0
  fi

  log "Rolling back from ${IMAGE_NAME}:${failed} to ${IMAGE_NAME}:${target}"
  rcompose "${failed}" stop app worker mcp || fail "cannot stop ${IMAGE_NAME}:${failed}"

  if [[ "${downgrade}" == yes ]]; then
    log "Downgrading the schema ${failed_rev} -> ${target_rev} in ${IMAGE_NAME}:${failed} (timeout ${MIGRATE_TIMEOUT}s)"
    rc=0
    timeout "${MIGRATE_TIMEOUT}" ssh "${SSH_OPTS[@]}" "${REMOTE}" "cd ${REMOTE_DIR} && \
      IMAGE_NAME=${IMAGE_NAME} TAG=${failed} ${COMPOSE[*]} run --rm --no-deps ${DOWNGRADE_DRILL_ENV:+${DOWNGRADE_DRILL_ENV} }app alembic downgrade ${target_rev}" \
      || rc=$?
    if (( rc != 0 )); then
      # A timeout is not a refusal: the one transaction never committed, so the schema is
      # unchanged and the cause is the database or its connection, not the data.
      local reason="failed (most likely it would lose data written since the deploy). ${failed} is running again. Restoring needs a vps-infra dump - see docs/operations.md"
      if (( rc == 124 )); then
        remove_oneoffs
        reason="did not finish within ${MIGRATE_TIMEOUT}s and was rolled back (the schema is still ${failed_rev}) - check the database and its connection, not the data. ${failed} is running again"
      fi
      log "Bringing ${IMAGE_NAME}:${failed} back up"
      rssh "cd ${REMOTE_DIR} && cp releases/${failed}/compose.yaml releases/${failed}/compose.prod.yaml ." || true
      rcompose "${failed}" up -d --remove-orphans || true
      fail "ROLLBACK REFUSED: the schema downgrade to ${target_rev} ${reason}"
    fi
  fi

  log "Starting ${IMAGE_NAME}:${target}"
  activate_release "${target}"
  wait_healthy "${target}" \
    || fail "rollback: ${IMAGE_NAME}:${target} did not become healthy (${HEALTH_STATUS})"
  log "Restoring the admin UI of ${target} into ~/${REMOTE_DIR}/web"
  sync_web "${target}"
  record_deployed "${target_commit}"
  rssh "cd ${REMOTE_DIR} && printf '%s\\n' '${target}' > CURRENT.tmp && mv CURRENT.tmp CURRENT && rm -f PREVIOUS" \
    || fail "cannot move the pointers in ~/${REMOTE_DIR} (CURRENT=${target}, no PREVIOUS)"
  ROLLED_BACK_TO="${target}"
  ROLLED_BACK_COMMIT="${target_commit}"
  run_smokes "${target}" "${target_commit}" --no-oauth
  DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_local_record rolled-back "${target_commit}" "${target}" "${failed}"
}

if [[ "${MODE}" == rollback ]]; then
  log "Opening one SSH connection to ${REMOTE} for the rollback"
  open_master
  rollback_to_previous manual
  log "Rolled back to ${IMAGE_NAME}:${ROLLED_BACK_TO} (${ROLLED_BACK_COMMIT}); there is no PREVIOUS any more - the next step is a deploy"
  exit 0
fi

# The admin UI is built locally too, before anything touches the network, with the commit
# as its bss-build marker - the web smoke test reads it back through Caddy.
log "Building the admin UI (frontend/dist, build ${TAG})"
./scripts/task-compose --deploy-web-build "${TAG}" </dev/null || fail "the UI build failed (run make deploy-web-install if node_modules is out of date)"
grep -q "<meta name=\"bss-build\" content=\"${TAG}\"" frontend/dist/index.html \
  || fail "frontend/dist/index.html does not carry build ${TAG}"

# DRY_RUN=1: every local check above has passed; list exactly what the rsync calls of a
# real deploy would ship (the same functions, --dry-run into a local scratch directory),
# prove the smoke credential is not among it, and stop before any network step.
if [[ "${DRY_RUN:-}" == 1 ]]; then
  stage="$(mktemp -d)"
  mkdir -p "${stage}/release/web" # --dry-run creates nothing; the real one exists first
  listing="$(mktemp)"
  if ! { ship_config "${stage}" --dry-run --out-format='%n' &&
         ship_release "${stage}/release" --list "releases/${TAG}/"; } >"${listing}"; then
    rm -rf "${stage}" "${listing}"
    fail "dry run: an rsync call failed (see above)"
  fi
  shipped="$(sort -u "${listing}")"
  rm -rf "${stage}" "${listing}"
  log "Dry run: a deploy of ${TAG} would ship $(wc -l <<<"${shipped}") paths (.env.prod lands as ~/bss/.env):"
  grep -v '/web/assets/' <<<"${shipped}" | sed 's/^/    /'
  printf '    ... and %s files under releases/%s/web/assets/\n' "$(grep -c '/web/assets/.' <<<"${shipped}")" "${TAG}"
  ! grep -q 'env\.smoke' <<<"${shipped}" || fail ".env.smoke would be shipped"
  log "Dry run OK: .env.smoke is not shipped; nothing touched the network"
  exit 0
fi

log "Opening one SSH connection to ${REMOTE} for the whole deploy"
open_master

log "Checking that the bss-edge and bss-db networks from vps-infra exist on ${REMOTE}"
# Postgres and Caddy live in the vps-infra project, which also creates these networks.
# Without them compose fails with a vaguer error, so check up front.
for net in bss-edge bss-db; do
  rssh "docker network inspect ${net} >/dev/null 2>&1" \
    || fail "network '${net}' not found on ${REMOTE} - deploy vps-infra first"
done
SERVER_NOTE="nothing changed on the server; the running release is still serving"

NEW_REV="$(alembic_rev_of "${DEPLOY_COMMIT}")"
[[ -n "${NEW_REV}" ]] || fail "cannot read the alembic revision of ${DEPLOY_COMMIT} (alembic/versions/)"

log "Building ${IMAGE_NAME}:${TAG} locally (runtime stage)"
docker build --target runtime -t "${IMAGE_NAME}:${TAG}" . || fail "docker build failed (see above)"

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
  docker save "${IMAGE_NAME}:${TAG}" | gzip -1 | rssh 'gunzip | docker load' \
    || fail "shipping the image to ${REMOTE} failed"
fi

bootstrap_release
OLD_CURRENT="$(remote_cat CURRENT)"
OLD_PREVIOUS="$(remote_cat PREVIOUS)"
[[ -z "${OLD_CURRENT}" ]] || SERVER_NOTE="nothing changed on the server; ${OLD_CURRENT} is still serving"

log "Syncing compose files"
rssh "mkdir -p ${REMOTE_DIR}" || fail "cannot create ~/${REMOTE_DIR} on ${REMOTE}"
ship_config "${REMOTE}:${REMOTE_DIR}" || fail "syncing the compose files failed"
record_release "${TAG}" "${DEPLOY_COMMIT}" "${NEW_REV}"

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
  IMAGE_NAME=${IMAGE_NAME} TAG=${TAG} ${COMPOSE[*]} run --rm --no-deps ${MIGRATE_DRILL_ENV:+${MIGRATE_DRILL_ENV} }app alembic upgrade head" \
  || migrate_rc=$?
if (( migrate_rc == 124 )); then
  remove_oneoffs
  fail "migrations did not finish within ${MIGRATE_TIMEOUT}s - the database is most likely unreachable.
  Check ${REMOTE_DIR}/.env on ${REMOTE} (POSTGRES_PASSWORD must be the bss role's password in vps-infra)
  and that the vps-infra postgres is running and attached to the 'bss-db' network:
    docker network inspect bss-db --format '{{range .Containers}}{{.Name}} {{end}}'"
elif (( migrate_rc != 0 )); then
  fail "database migration failed (see output above)"
fi

# The point of no return: from here on every fail() rolls back.
log "Starting the app"
SERVER_NOTE=""
ROLLBACK_ARMED=1
activate_release "${TAG}" "${UP_DRILL_ENV}"

log "Waiting for the app, worker and mcp containers to become healthy"
wait_healthy "${TAG}" || fail "containers did not become healthy (${HEALTH_STATUS})"
drill_point health "the health gate"

log "Recording the running commit in ~/${REMOTE_DIR}/DEPLOYED"
record_deployed "${DEPLOY_COMMIT}"
check_deployed "${DEPLOY_COMMIT}" "${TAG}"

DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_local_record running

smoke_backend "${TAG}"

# The UI goes live only after the app that serves its API is up, synced from this
# release's stored copy (sync_web), so ~/bss/web always equals a stored release.
log "Shipping the admin UI into ~/${REMOTE_DIR}/web (synced into the directory, never replaced)"
sync_web "${TAG}"
drill_point web-sync "the UI sync"

smoke_edge "${TAG}"

# Every gate passed: disarm, then move the pointers (a re-deploy of CURRENT keeps PREVIOUS).
ROLLBACK_ARMED=0
if [[ -n "${OLD_CURRENT}" && "${OLD_CURRENT}" != "${TAG}" ]]; then
  NEW_PREVIOUS="${OLD_CURRENT}"
  rssh "cd ${REMOTE_DIR} && printf '%s\\n' '${OLD_CURRENT}' > PREVIOUS.tmp && mv PREVIOUS.tmp PREVIOUS && printf '%s\\n' '${TAG}' > CURRENT.tmp && mv CURRENT.tmp CURRENT" \
    || fail "cannot move the pointers in ~/${REMOTE_DIR} (CURRENT=${TAG}, PREVIOUS=${OLD_CURRENT})"
else
  NEW_PREVIOUS="${OLD_PREVIOUS}"
  write_current "${TAG}"
fi
log "Releases: CURRENT=${TAG}, PREVIOUS=${NEW_PREVIOUS:-none}"

# Each OAuth smoke leaves a client registration (no deletion endpoint); remove the smoke's
# own ones without a live token, so MCP_MAX_CLIENTS never fills (docs/decisions/mcp.md).
# Only a warning on failure: everything is deployed and checked by now.
log "Pruning the smoke's MCP client registrations"
{ drill_warn && rcompose "${TAG}" exec -T mcp python -m bss_mcp.tokens prune-smoke-clients; } \
  || log "WARNING: pruning the smoke client registrations failed; harmless now, see docs/mcp.md"

log "Pruning old ${IMAGE_NAME} images on ${REMOTE} (keeping ${KEEP_IMAGES} and CURRENT/PREVIOUS, nothing else is touched)"
{ drill_warn && prune_images "${TAG}" "${NEW_PREVIOUS}"; } || log "WARNING: pruning old images failed; harmless now"
log "Pruning release directories other than CURRENT and PREVIOUS"
{ drill_warn && prune_releases "${TAG}" "${NEW_PREVIOUS}"; } || log "WARNING: pruning ~/${REMOTE_DIR}/releases failed; harmless now"

write_local_record passed

log "Deployed ${IMAGE_NAME}:${TAG} — app at 127.0.0.1:18081 on ${REMOTE}, UI at ${WEB_URL}"
