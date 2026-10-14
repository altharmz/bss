#!/usr/bin/env bash
# Regression cases for the Codex hooks: Codex-shaped PreToolUse payloads fed to the hook
# commands exactly as .codex/hooks.json writes them, run through `sh -c` from a nested
# directory and from outside the repository. Exit 0 if every case behaves as expected.
# Proves the scripts' policy, not that a Codex client loads them (docs/decisions/harness.md).
set -uo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
outside="$(mktemp -d)"
stderr="$(mktemp)"
trap 'rm -rf "$outside" "$stderr"' EXIT
hook() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["hooks"]["PreToolUse"][int(sys.argv[2])]["hooks"][0]["command"])' "$repo/.codex/hooks.json" "$1"; }
bash_cmd="$(hook 0)"
patch_cmd="$(hook 1)"

failed=0
run() {  # expected-rc dir hook-command payload label
  (cd "$2" && printf '%s' "$4" | env -u CLAUDE_PROJECT_DIR -u CLAUDE_CODE_REMOTE sh -c "$3" >/dev/null 2>"$stderr")
  local got=$?
  if [[ $got == "$1" ]]; then
    echo "ok   rc=$got $5"
  else
    echo "FAIL rc=$got want $1: $5"
    cat "$stderr"
    failed=1
  fi
}
payload() {  # tool_name command [cwd]
  python3 -c 'import json,sys;print(json.dumps({"hook_event_name":"PreToolUse","tool_name":sys.argv[1],"cwd":sys.argv[3],"tool_input":{"command":sys.argv[2]}}))' "$1" "$2" "${3:-$repo}"
}
bashp() { payload Bash "$@"; }
patchp() { payload apply_patch "$@"; }
P() { printf '*** Begin Patch\n*** %s File: %s\n@@\n-a\n+b\n*** End Patch\n' "$1" "$2"; }

n="$repo/app/api"
run 0 "$n" "$bash_cmd" "$(bashp 'make test')" "bash: make test"
run 0 "$n" "$bash_cmd" "$(bashp 'make deploy')" "bash: make deploy alone (~/.codex/rules asks)"
run 0 "$n" "$bash_cmd" "$(bashp 'make lint test && git status')" "bash: two local targets"
run 2 "$n" "$bash_cmd" "$(bashp 'make test deploy')" "bash: deploy after test"
run 2 "$n" "$bash_cmd" "$(bashp 'make fmt; make lint clean')" "bash: clean second in a later make"
run 2 "$n" "$bash_cmd" "$(bashp 'make -k test verify')" "bash: verify after an option"
run 0 "$n" "$bash_cmd" "$(bashp 'echo make test deploy')" "bash: make as an echo argument"
run 2 "$n" "$bash_cmd" "$(bashp 'docker volume rm x')" "bash: the Claude guard still applies"
run 2 "$outside" "$bash_cmd" "$(bashp 'make test' "$outside")" "bash: outside the repo fails closed"

run 0 "$n" "$patch_cmd" "$(patchp "$(P Update app/api/bill.py)")" "patch: app file, relative"
run 0 "$n" "$patch_cmd" "$(patchp "$(P Update "$repo/tests/x.py")")" "patch: test file, absolute"
run 0 "$n" "$patch_cmd" "$(patchp "$(P Update bill.py)" "$n")" "patch: relative to a nested cwd"
run 0 "$n" "$patch_cmd" "$(patchp "$(P Add .env.example)")" "patch: .env.example is allowed"
run 0 "$n" "$patch_cmd" "$(patchp "$(P Add "$outside/x.txt")")" "patch: outside the repo"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update .env)")" "patch: .env"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update "$repo/.env.prod")")" "patch: .env.prod, absolute"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update Makefile)")" "patch: Makefile"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update compose.prod.yaml)")" "patch: compose.prod.yaml"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update ../../scripts/deploy.sh)" "$n")" "patch: scripts/ via ../"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Delete .claude/hooks/guard.sh)")" "patch: delete the Claude guard"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update .codex/hooks.json)")" "patch: .codex/"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update docs/progress/scope.yaml)")" "patch: scope.yaml"
run 2 "$n" "$patch_cmd" "$(patchp "$(printf '*** Begin Patch\n*** Update File: app/x.py\n*** Move to: scripts/x.py\n@@\n*** End Patch\n')")" "patch: move into scripts/"
run 2 "$n" "$patch_cmd" "$(patchp "$(P Update design-ref/a.html)")" "patch: design-ref/"
run 2 "$n" "$patch_cmd" 'not json' "patch: unparsable payload"
run 2 "$n" "$patch_cmd" "$(patchp '*** Begin Patch garbage')" "patch: no readable file header"
run 2 "$outside" "$patch_cmd" "$(patchp "$(P Update app/api/bill.py)" "$outside")" "patch: outside the repo fails closed"
exit $failed
