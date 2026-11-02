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
After explicit human approval of the concrete PR and exact current head in this
conversation, use `make merge-task HUMAN_APPROVAL=<PR-number>:<full-head-SHA>` as documented
in `docs/worktrees.md`. New commits require renewed approval; all other gates still apply.

Standing human approval, reaffirmed 2026-11-02: edits to all repository files are authorized,
including instruction/workflow files and files in linked task worktrees. This approval
persists across sessions; never ask again for repository edit consent. Follow the existing
secret/design-reference restrictions and human merge gates. Tool sandbox approval, when
required, is separate from edit consent.

Standing authorization: adding relevant task and PR progress, validation, blocker and
telemetry comments is authorized. Never ask for permission or confirmation to add these
comments. Prefer Make helpers where available. Required tool sandbox approvals remain
separate.

Save machine-observed usage statistics in the current issue's comments at the beginning
of work and when the PR is created. The Make lifecycle posts both checkpoints; verify
them and recover failures with `make telemetry-comment PHASE=start` or
`make telemetry-comment PHASE=pr`. Show the starting/latest counters and their observed
difference, participating clients/models/sessions, pricing provenance and explicit gaps.
Never guess counts or report unknown usage/cost as zero. On resume/handoff run
`make telemetry-collect`; bind the actual client session as described in
[telemetry-usage.md](docs/telemetry-usage.md). These shared rules also apply to Kimi.

`make new-priority-task` only creates an explicitly approved P0 issue first in Ready.
It does not start work; selection and claim remain in the separate `make start-task`.

For new work, strongly prefer `make start-task BSS_TASK_OWNER=codex` from clean primary
master. It selects the issue and worker slot and claims the board item. Do not manually
inspect the board, count statuses, select a Ready issue or claim an item before startup.
Use the returned issue, branch and worktree; stop and report a startup failure.

For requests such as "deliver the next task", "take the next task" or "start the next
task", run `make start-task BSS_TASK_OWNER=codex` directly from primary master after
reading the required instructions. Do not run preliminary GitHub CI, board, PR or Git
inventory commands and do not ask for confirmation. The startup helper owns preflight,
selection and claim; this explicit next-task path takes precedence over the general
continue protocol's preliminary master-CI check. If the user asks only to trigger
startup, report the result and stop; otherwise continue delivery in the returned worktree.
Required tool sandbox approvals and human merge gates still apply.

**Deployment is the human's, outside development** (the human, 2026-10-28): your work ends
at the merge to master. Never run `make deploy`/`verify`/`remote`, never track or suggest a
deploy.

## Task board

Read [docs/board.md](docs/board.md) before any board operation (statuses, project ids, `gh`
commands, merge and close rules; your label is `agent:codex`). On "continue", or at session
start without a concrete request, follow the protocol in
[.claude/commands/continue.md](.claude/commands/continue.md); a concrete request takes
precedence and does not trigger unrelated Ready work. Codex does not run Claude commands, so
open and follow that file. Resume known owned work in its task worktree; for new work,
strongly prefer `make start-task BSS_TASK_OWNER=codex` immediately from clean primary master.
Do not manually inspect the board or enumerate open PRs before startup. The helper is the
only Ready-selection mechanism. Use exactly the
returned issue, branch, slot and worktree, and fetch only the selected issue's body. Kimi uses
`make start-task BSS_TASK_OWNER=kimi JSON=1`. The short rules are in the
[Task board](CLAUDE.md#task-board) section of CLAUDE.md.
