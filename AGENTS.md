# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file, and
[docs/worktrees.md](docs/worktrees.md#normal-agent-workflow) is the workflow. Start new work
with `make start-task BSS_TASK_OWNER=codex`; your board label is `agent:codex`.

Codex specifics:

- `apply_patch` is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared
  shell guard (`.claude/hooks/guard.py --codex`) and `.codex/patch-guard.sh` (never edit `.env*` or
  `design-ref/`).
- Run `make` targets as a bare command (`make test`, `make lint`): no `>` redirect, pipe,
  `&&` or `;` in the same call. `~/.codex/rules` allows `make` only when the call is exactly
  that; otherwise Codex asks for approval. Read the output from the tool result.
- `make commit` is permanently authorized by `.codex/rules/commit.rules`. A commit also writes
  the primary repository's `.git`, so the executor's writable roots must include the primary
  checkout and the worker root (`/home/petrg/projects/bss` and
  `/home/petrg/projects/bss-agents`). A filesystem denial is not a reason to disable a guard
  or ask for edit consent again.

Make first, in practice (the rules for every client, such as long targets, body files
outside the worktree and subagents, are in
[docs/worktrees.md](docs/worktrees.md#normal-agent-workflow)):

- **Discover, don't guess.** `make help` lists every target; read it instead of the Makefile.
  `make show-task` prints the claimed issue (body, labels, comments). Raw `gh` is for
  `gh issue comment` only. Bare `git add`, `git commit` and `git push` are blocked for Codex
  by the guard (`.claude/hooks/guard.py --codex`): use `make commit` and `make push`. When no
  target covers a step, use the plain command and say so in the summary.
- **Rules.** `.codex/rules/default.rules` is the tracked command allow list; install it to
  `~/.codex/rules/default.rules` (see the file). `make` is allowed, nothing else writes Git.
