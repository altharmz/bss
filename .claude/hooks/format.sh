#!/usr/bin/env bash
# PostToolUse hook: format and lint the file that was just edited, so mistakes surface
# immediately instead of ten steps later.
#
# Runs ruff in the `tools` compose service - the container is the single source of truth
# for the ruff version, shared with `make fmt`. Exit codes (PostToolUse never blocks; exit 2
# feeds stderr back to Claude):
#   0  not a file a formatter service mounts, or formatted and clean - prints nothing
#   2  ruff findings it could not fix, or the hook itself failed to run
set -uo pipefail

fail() {
  echo "format hook FAILED to run: $*" >&2
  exit 2
}

payload="$(cat)"
file="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')" \
  || fail "could not parse the hook payload"

project="${CLAUDE_PROJECT_DIR:?}"
abs="$(realpath -m -- "$file")"
[[ "$abs" == "$project"/* ]] || exit 0
[[ -f "$abs" ]] || exit 0
rel="${abs#"$project"/}"

# The admin UI: prettier in the offline web container (the same version make web-lint
# checks with). .prettierignore skips generated code and the lockfile. node_modules out of
# date is a failure ("run make web-install"), never an install.
if [[ "$rel" == frontend/* && "$rel" =~ \.(ts|tsx|js|mjs|css|json|html)$ ]]; then
  cd "$project" || fail "cannot cd to $project"
  out="$(./scripts/task-compose run --rm -T --no-deps -e f="${rel#frontend/}" web-offline \
    sh -c 'sh scripts/check-deps.sh && npx prettier --write --log-level warn "$f"' 2>&1)"
  rc=$?
  out="$(grep -v '^ *Container ' <<<"$out")"
  [[ $rc -eq 0 ]] && exit 0
  fail "prettier on $rel: $(tail -n 20 <<<"$out")"
fi

[[ "$file" == *.py ]] || exit 0

# The service that mounts the file writable (see compose.override.yaml): `tools` for the
# application code, `scripts-tools-fmt` for the harness scripts (as make scripts-fmt). A
# Python file no service mounts (.claude/hooks/, ops/) is not formatted here and is not an
# error: the hook has nothing to say about it.
case "$rel" in
  app/* | bss_mcp/* | tests/* | alembic/* | loadtest/*) service=tools ;;
  scripts/*) service=scripts-tools-fmt ;;
  *) exit 0 ;;
esac

cd "$project" || fail "cannot cd to $project"

# --force-exclude: honour extend-exclude (alembic/versions) even for explicit paths.
# One container start: format, then fix what ruff check can.
out="$(./scripts/task-compose run --rm -T --no-deps -e f="$rel" "$service" \
  sh -c 'ruff format --quiet --force-exclude "$f" &&
         ruff check --fix --quiet --output-format concise --force-exclude "$f"' 2>&1)"
rc=$?
# compose's own progress lines ("Container ... Created") are noise here
out="$(grep -v '^ *Container ' <<<"$out")"

case $rc in
  0) exit 0 ;;
  # ruff check: 1 = findings remain. Anything else (docker/compose error, missing image or
  # service, ruff format failure) means the hook did not do its job.
  1)
    if grep -qE '^[^ ].*:[0-9]+:[0-9]+: [A-Z]+[0-9]+ ' <<<"$out"; then
      printf 'ruff findings in %s:\n%s\n' "$rel" "$out" >&2
      exit 2
    fi
    fail "$(tail -n 20 <<<"$out")"
    ;;
  *) fail "exit $rc: $(tail -n 20 <<<"$out")" ;;
esac
