#!/usr/bin/env bash
# PostToolUse hook: format and lint the file that was just edited, so mistakes surface
# immediately instead of ten steps later.
set -uo pipefail

payload="$(cat)"
file="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[[ "$file" == *.py ]] || exit 0
[[ -f "$file" ]] || exit 0

if command -v ruff >/dev/null 2>&1; then
  ruff format "$file" >/dev/null 2>&1
  ruff check --fix "$file" 2>&1 | tail -n 20
else
  docker compose run --rm --no-deps -T app ruff format "${file#./}" >/dev/null 2>&1
  docker compose run --rm --no-deps -T app ruff check --fix "${file#./}" 2>&1 | tail -n 20
fi
exit 0
