#!/usr/bin/env bash
# Codex Bash hook: hands the payload (tool_input.command, as Claude's) to the shared guard.
set -euo pipefail
export CLAUDE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec bash "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.sh"
