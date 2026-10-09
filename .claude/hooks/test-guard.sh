#!/usr/bin/env bash
# Runs the regression cases in guard-cases.txt through guard.sh, exactly as Claude Code
# calls it (the tool call as JSON on stdin). Exit 0 if every case behaves as expected.
# Called by scripts/pre-commit.sh whenever .claude/ changes.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "${here}/../.." && pwd)}"
cases="${here}/guard-cases.txt"

total=0
failed=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" || "${line}" == \#* ]] && continue
  expected="${line%% *}"
  command="${line#* }"
  command="${command//\$CLAUDE_PROJECT_DIR/${CLAUDE_PROJECT_DIR}}"
  # <expected>@cloud: the case runs in cloud mode (BSS_CLOUD=1); every other case runs
  # with BSS_CLOUD removed, so the suite means the same on the workstation and in the cloud.
  mode=(env -u BSS_CLOUD)
  if [[ "${expected}" == *@cloud ]]; then
    mode=(env BSS_CLOUD=1)
    expected="${expected%@cloud}"
  fi
  case "${expected}" in
    allow-ml | block-ml)
      command="${command//\\n/$'\n'}"
      expected="${expected%-ml}"
      ;;
    allow | block) ;;
    *)
      echo "bad case (expected allow/block): ${line}" >&2
      failed=$((failed + 1))
      continue
      ;;
  esac
  payload="$(python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.argv[1]}}))' "${command}")"
  printf '%s' "${payload}" | "${mode[@]}" "${here}/guard.sh" >/dev/null 2>&1
  rc=$?
  total=$((total + 1))
  if [[ "${expected}:${rc}" != "allow:0" && "${expected}:${rc}" != "block:2" ]]; then
    printf 'FAIL expected %s (%s), got exit %s: %s\n' "${expected}" "${mode[*]}" "${rc}" "${command}" >&2
    failed=$((failed + 1))
  fi
done <"${cases}"

if ((failed > 0)); then
  printf 'guard regression: %d of %d cases failed\n' "${failed}" "${total}" >&2
  exit 1
fi
printf 'guard regression: all %d cases pass\n' "${total}"
