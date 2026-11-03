#!/usr/bin/env bash
# Runs guard-cases.txt through guard.sh as Claude Code calls it (JSON on stdin).
# Called by scripts/pre-commit.sh whenever .claude/ changes.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
failed=0
while IFS=$'\t' read -r expected command; do
  [[ -z "${expected}" || "${expected}" == \#* ]] && continue
  python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.argv[1]}}))' "${command}" |
    "${here}/guard.sh" >/dev/null 2>&1
  [[ $? == 2 ]] && got=block || got=allow
  if [[ "${got}" != "${expected}" ]]; then
    echo "FAIL: expected ${expected}, got ${got}: ${command}"
    failed=$((failed + 1))
  fi
done <"${here}/guard-cases.txt"
((failed == 0)) && echo "guard: all cases pass" || { echo "guard: ${failed} failed"; exit 1; }
