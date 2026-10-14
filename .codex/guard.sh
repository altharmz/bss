#!/usr/bin/env bash
# Codex Bash hook inputs use tool_input.command, as the Claude guard expects.
set -euo pipefail

export CLAUDE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
payload="$(cat)"

# ~/.codex/rules asks before `make deploy` etc., but a prefix rule sees only the first
# target: `make test deploy` would run unasked. A VPS/destructive target must come first.
python3 - "$payload" <<'PY' || exit 2
import json, shlex, sys
RISKY = {"deploy", "verify", "remote", "ssh", "tunnel", "clean", "spec-verify-upstream", "hooks"}
try:
    cmd = json.loads(sys.argv[1]).get("tool_input", {}).get("command", "")
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
except Exception:
    sys.exit(0)  # unparsable: the Claude guard's fallback below decides
targets, at_start = None, True  # at_start: the next word is a command name
for token in tokens + [";"]:
    if token and set(token) <= set(";&|()"):
        if targets is not None and any(t in RISKY for t in targets[1:]):
            print("BLOCKED by .codex/guard.sh: run a VPS or destructive make target "
                  "(deploy, verify, remote, ssh, tunnel, clean, ...) as the first and only "
                  "target, so Codex's rules ask the human first.", file=sys.stderr)
            sys.exit(1)
        targets, at_start = None, True
    elif at_start:
        targets, at_start = ([] if token == "make" else None), False
    elif targets is not None and not token.startswith("-") and "=" not in token:
        targets.append(token)
PY

exec bash "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.sh" <<<"$payload"
