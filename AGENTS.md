# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md) before working in this repository. It is the
shared source of project instructions for Claude Code and Codex. Read the relevant
documents under `docs/decisions/` before changing an area, as CLAUDE.md requires.

For Codex, use `apply_patch` as the equivalent of Claude's Edit/Write tools. All
project constraints and approval requirements still apply.

## Automatic shell guard

`.codex/hooks.json` registers a `PreToolUse` hook for Bash (including Codex unified
exec). `.codex/guard.sh` sets `CLAUDE_PROJECT_DIR` to this repository and delegates
the unchanged hook payload to `.claude/hooks/guard.sh`. The Claude guard remains
the single source of shell policy; its exit code 2 blocks a command in Codex too.

Project hooks require a trusted project and explicit hook trust in a Codex client
that supports hooks. Restart or start a fresh session after installing them, and
review/trust the hook when the client requests it. Do not claim automatic
enforcement until the hook has been observed firing in that client.

This adapter does not install formatting, startup setup, or Claude's permission
rules. It preserves `CLAUDE_CODE_REMOTE` if supplied; it does not infer Codex cloud
mode or run Claude's cloud setup. Follow the appropriate environment instructions
explicitly until a separate cloud adapter is configured.
