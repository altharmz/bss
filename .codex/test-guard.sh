#!/usr/bin/env bash
# Runs .codex/guard-cases.txt through .codex/guard.sh and .codex/patch-guard-cases.txt through
# .codex/patch-guard.sh, as Codex calls them (JSON on stdin).
# Called by scripts/pre-commit.sh whenever .codex/ changes.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
failed=0
while IFS=$'\t' read -r expected command; do
  [[ -z "${expected}" || "${expected}" == \#* ]] && continue
  command="$(printf '%b' "${command}")" # a literal \n in a case is a newline
  python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.argv[1]}}))' "${command}" |
    bash "${here}/guard.sh" >/dev/null 2>&1
  [[ $? == 2 ]] && got=block || got=allow
  if [[ "${got}" != "${expected}" ]]; then
    echo "FAIL: expected ${expected}, got ${got}: ${command}"
    failed=$((failed + 1))
  fi
done <"${here}/guard-cases.txt"

# The apply_patch guard: a one-file patch per case, as Codex sends it (JSON on stdin).
# $CONFIG in a path is the caller's configuration directory, as an absolute path.
config="${XDG_CONFIG_HOME:-$HOME/.config}"
root="$(cd "${here}/.." && pwd -P)"
while IFS=$'\t' read -r expected operation path cwd; do
  [[ -z "${expected}" || "${expected}" == \#* ]] && continue
  path="${path//\$CONFIG/${config}}"
  python3 -c 'import json,sys; print(json.dumps({"cwd": sys.argv[3], "tool_input": {"command": "*** Begin Patch\n*** %s: %s\n*** End Patch\n" % (sys.argv[1], sys.argv[2])}}))' \
    "${operation}" "${path}" "${root}/${cwd}" |
    bash "${here}/patch-guard.sh" >/dev/null 2>&1
  [[ $? == 2 ]] && got=block || got=allow
  if [[ "${got}" != "${expected}" ]]; then
    echo "FAIL (patch): expected ${expected}, got ${got}: ${operation}: ${path} (cwd ${cwd})"
    failed=$((failed + 1))
  fi
done <"${here}/patch-guard-cases.txt"
((failed == 0)) && echo "codex guards: all cases pass" || { echo "codex guards: ${failed} failed"; exit 1; }
