#!/usr/bin/env bash
# Codex Bash hook inputs use tool_input.command, as the Claude guard expects.
set -euo pipefail

export CLAUDE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec bash "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.sh"
