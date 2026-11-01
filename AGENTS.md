# Codex project instructions

Read and follow [CLAUDE.md](CLAUDE.md); it is the shared instruction file. `apply_patch`
is the equivalent of Claude's Edit/Write. `.codex/hooks.json` runs the shared shell guard
(`.claude/hooks/guard.sh`) and `.codex/patch-guard.sh` (never edit `.env*` or `design-ref/`).

Run `make` targets as a bare command (`make test`, `make lint`): no `>`
redirect, pipe, `&&`, `;` or other command in the same call. `~/.codex/rules` allows
`make` only when the call is exactly that, otherwise Codex asks for approval. The output
comes back to you anyway; read it from the tool result, not from a log file.

Follow the [canonical Make lifecycle](docs/worktrees.md#normal-agent-workflow) and
[shared AI guidance](docs/worktrees.md#ai-workflow-guidance), including client identity,
tool choice, engineering review, recovery, checks, human gates and completion reporting.
Commit and publish when ready; do not ask for approval. Human merge gates still apply.

**Deployment is the human's, outside development** (the human, 2026-10-28): your work ends
at the merge to master. Never run `make deploy`/`verify`/`remote`, never track or suggest a
deploy.

## Task board

Read [docs/board.md](docs/board.md) before any board operation (statuses, project ids, `gh`
commands, merge and close rules; your label is `agent:codex`). On "continue", or at session
start without a concrete request, follow the protocol in
[.claude/commands/continue.md](.claude/commands/continue.md); a concrete request takes
precedence and does not trigger unrelated Ready work. Codex does not run Claude commands, so
open and follow that file. The short rules are in the [Task board](CLAUDE.md#task-board) section
of CLAUDE.md.
