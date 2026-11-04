#!/usr/bin/env bash
# Codex Bash hook: (1) Codex-only rule: git add/commit/push run through `make commit` and
# `make push`, never bare (the Make targets validate files and run the hooks); (2) hands the
# payload (tool_input.command, as Claude's) to the shared guard. Exit 2 blocks.
set -euo pipefail
export CLAUDE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
payload="$(cat)"

if ! printf '%s' "$payload" | python3 -c '
import json, re, sys

cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
# Prose does not run: drop heredoc bodies (unless fed to a shell) and quoted strings.
out, end = [], None
for line in cmd.split("\n"):
    if end is not None:
        end = None if line.strip() == end else end
        continue
    out.append(line)
    m = re.search(r"<<-?\s*([\x27\"]?)(\w+)\1", line)
    if m:
        end = m.group(2)
text = "\n".join(out)
text = re.sub(r"\x27[^\x27]*\x27|\"(?:\\\\.|[^\"\\\\])*\"", "\x27\x27", text)
if re.search(r"(?:^|[;&|(]\s*|\bxargs\s+|\bsudo\s+)git(?:\s+-\S+(?:\s+\S+)?)*?\s+(?:add|commit|push)\b", text, re.M):
    sys.exit(1)
' 2>/dev/null; then
  echo "BLOCKED by .codex/guard.sh: use make commit MESSAGE=... FILES='[...]' and make push; bare git add/commit/push skip the task validation." >&2
  exit 2
fi

printf '%s' "$payload" | exec bash "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.sh"
