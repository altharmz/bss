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
The adapter also blocks a VPS or destructive make target (`deploy`, `verify`,
`remote`, `ssh`, `tunnel`, `clean`, ...) that is not the first target of its `make`
call: run such a target alone, so the user's Codex rules ask first.

## Edit guard

A second `PreToolUse` hook, `.codex/patch-guard.sh`, checks every path in an
`apply_patch`. `.env`, `.env.prod`, `.env.smoke` and `design-ref/` are never edited.
Harness files (`.claude/`, `.codex/`, `compose*.yaml`, `Makefile`, `scripts/`)
need the user's OK: when blocked, show the diff and ask
the user to apply or approve it. Never edit them another way (a shell redirect,
`sed -i`, a script) - CLAUDE.md forbids that, and the shell guard blocks it.

Progress scope (`docs/progress/scope.yaml`) is editable within authorized feature
work. Preserve existing items and exclusions unless the task changes their scope;
generate `docs/PROGRESS.md` and `docs/progress/status.json` with `make progress`.

Both hooks fail closed when started outside the repository. Regression cases:
`bash .codex/test-hooks.sh`.

Project hooks require a trusted project and explicit hook trust in a Codex client
that supports hooks. Restart or start a fresh session after installing them, and
review/trust the hook when the client requests it. Do not claim automatic
enforcement until the hook has been observed firing in that client.

These hooks do not install formatting, startup setup, or Claude's remaining
permission rules. The adapter preserves `CLAUDE_CODE_REMOTE` if supplied; it does not infer Codex cloud
mode or run Claude's cloud setup. Follow the appropriate environment instructions
explicitly until a separate cloud adapter is configured.
