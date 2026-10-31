#!/usr/bin/env bash
# Runs guard-cases.txt through guard.sh as Claude Code calls it (JSON on stdin).
# Called by scripts/pre-commit.sh whenever .claude/ changes.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
failed=0
while IFS=$'\t' read -r expected command; do
  [[ -z "${expected}" || "${expected}" == \#* ]] && continue
  run=(env -u CLAUDE_CODE_REMOTE)
  if [[ "${expected}" == cloud-* ]]; then run=(env CLAUDE_CODE_REMOTE=true); expected="${expected#cloud-}"; fi
  python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.argv[1]}}))' "${command}" |
    "${run[@]}" "${here}/guard.sh" >/dev/null 2>&1
  [[ $? == 2 ]] && got=block || got=allow
  if [[ "${got}" != "${expected}" ]]; then
    echo "FAIL: expected ${expected}, got ${got}: ${command}"
    failed=$((failed + 1))
  fi
done <"${here}/guard-cases.txt"
# Replay the original multiline source-writing shape without executing its payload.
python3 - "$here/guard.sh" <<'SOURCE' || failed=$((failed + 1))
import json, os, subprocess, sys
command = """python3 - <<'PY'
from pathlib import Path
Path("task_git.py").write_text('''
configured = subprocess.run(
    ["git", "-C", str(root), "config", "--get", "core.hooksPath"], capture_output=True
)
require(configured.returncode == 1, "custom core.hooksPath is unsupported; run make hooks")
''')
PY"""
env = dict(os.environ)
env.pop("CLAUDE_CODE_REMOTE", None)
for text, expected in ((command, 0), (command + "\ngit -c core.hooksPath=/dev/null commit -m x", 2)):
    result = subprocess.run(
        ["bash", sys.argv[1]], input=json.dumps({"tool_input": {"command": text}}),
        text=True, capture_output=True, env=env,
    )
    if result.returncode != expected:
        sys.exit("FAIL: multiline source-write hook-path regression")
SOURCE
((failed == 0)) && echo "guard: all cases pass" || { echo "guard: ${failed} failed"; exit 1; }
