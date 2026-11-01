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
Use Make targets wherever they cover the operation, including `make review-task` for
routine task-state checks and `make check-pr` for PR readiness. Direct commands remain
available for source investigation and operations without a matching target.
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
open and follow that file. After owned Doing work and open PRs have been resumed or ruled out,
run `make start-task` immediately from clean primary master. Do not manually list, compare,
rank, or select Ready issues; the helper is the only Ready-selection mechanism. Use exactly the
returned issue, branch, slot and worktree, and fetch only the selected issue's body. Kimi uses
`make start-task BSS_TASK_OWNER=kimi JSON=1`. The short rules are in the
[Task board](CLAUDE.md#task-board) section of CLAUDE.md.
