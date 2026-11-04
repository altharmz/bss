# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file, and
[docs/worktrees.md](docs/worktrees.md#normal-agent-workflow) is the workflow. Start new work
with `make start-task BSS_TASK_OWNER=codex`; your board label is `agent:codex`.

Codex specifics:

- `apply_patch` is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared
  shell guard (`.claude/hooks/guard.sh`) and `.codex/patch-guard.sh` (never edit `.env*` or
  `design-ref/`).
- Run `make` targets as a bare command (`make test`, `make lint`): no `>` redirect, pipe,
  `&&` or `;` in the same call. `~/.codex/rules` allows `make` only when the call is exactly
  that; otherwise Codex asks for approval. Read the output from the tool result.
- `make commit` is permanently authorized by `.codex/rules/commit.rules`. A commit also writes
  the primary repository's `.git`, so the executor's writable roots must include the primary
  checkout and the worker root (`/home/petrg/projects/bss` and
  `/home/petrg/projects/bss-agents`). A filesystem denial is not a reason to disable a guard
  or ask for edit consent again.

Make first, in practice:

- **Discover, don't guess.** `make help` lists every target; read it instead of the Makefile.
  `make show-task` prints the claimed issue (body, labels, comments). Raw `gh` is for
  `gh issue comment` only. Bare `git add`, `git commit` and `git push` are blocked by
  `.codex/guard.sh`: use `make commit` and `make push`. When no target covers a step, use the
  plain command and say so in the summary.
- **Long targets outlive a tool call.** `make commit` (the hook runs 2 to 12 minutes),
  `make sync-task`, `make test`, `make web-shots` and `make check` take longer than one
  call. Start them in a persistent or background session and wait for the final status line.
  If a call was killed, run `make review-task` before trying again; never start a second copy
  while one runs, and never pipe them (a pipe hides the exit status).
- **After `make sync-task` pulls in UI changes**, run `make web-install` before committing,
  or the hook stops at `web-lint`. If master moves while the hook runs, `create-pr` asks for
  another `make sync-task`; run it again and go straight on to `make push` and `make check-pr`.
- **Body files** (`PR_BODY_FILE`, `MESSAGE_FILE`, `FILES_FILE`) go in `/tmp`, never inside the
  worktree: `make finish-task` refuses unknown files (even under `.task/`).
- **Subagents** work in the returned task worktree only, with the same targets. Give them the
  full task and check back at most every few minutes; do not poll in a loop. You run
  `make review-task` and read the diff yourself before `make commit`.
- **Rules.** `.codex/rules/default.rules` is the tracked command allow list; install it to
  `~/.codex/rules/default.rules` (see the file). `make` is allowed, nothing else writes Git.
