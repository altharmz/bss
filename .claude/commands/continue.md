---
description: Resume known work or use make start-task to select and claim the next issue
---

Follow this when the human says "continue" or starts a session without a concrete task. Read
[docs/board.md](../../docs/board.md) first for board policy.
A concrete request takes precedence: check its local state and only the board or PR state it
needs. Do not run the full startup sequence or take unrelated Ready work.

An explicit request to "deliver the next task", "take the next task" or "start the
next task" uses the direct startup path: after reading required instructions, run
`make start-task BSS_TASK_OWNER=<client>` from primary master without preliminary
GitHub CI, board, PR or Git inventory commands or confirmation questions. The helper
owns startup preflight, selection and claim. This path takes precedence over the
preliminary master-CI check below. If asked only to trigger startup, report its result
and stop; otherwise continue delivery in the returned worktree. Required tool sandbox
approvals and human merge gates still apply.

For the full protocol, build the picture before touching code. **First, check `master`**:
`gh run list --branch master --limit 5`. If the latest push run or the nightly full run failed,
revert the offending PR (a branch and a PR), open an issue for it, then carry on - see board.md
"Red master". Do not enumerate board items, count statuses or inspect candidate issue
bodies as a startup prerequisite. Issue selection and claim belong to `make start-task`.

Then, in this order:

1. Locate existing work with only the read-only inspection needed. In an identified task
   checkout, use `make review-task` for routine local-state checks. Use direct Git commands
   for primary/task discovery or details it does not expose (such as unpushed commits or
   stashes), when relevant; do not run a blanket Git inventory before every Make target.
2. Resume a known task you own in its existing worktree. Use `make check-pr` for that
   task's PR; failing checks or requested changes come before new work. Do not perform a
   manual board or open-PR inventory to find new work.
   Run `make telemetry-collect` to bind the actual resumed client session and capture
   registered sessions. Kimi agents must always run
   `make telemetry-collect CLIENT=kimi SESSION_ID=<actual-session-uuid>` instead, with the
   actual session ID. Follow `docs/telemetry-usage.md` when session identity is unavailable.
   Verify the issue's starting statistics comment and, after PR creation, its second
   statistics comment showing the observed difference. Retry posting with
   `make telemetry-comment PHASE=start` or `PHASE=pr`; never guess missing counts.
3. For new work, strongly prefer `make start-task BSS_TASK_OWNER=codex` for Codex, or
   `make start-task BSS_TASK_OWNER=claude` for Claude, from clean primary master.
   Use the returned issue and worktree. The helper claims the agent label and Doing
   status as part of startup; do not repeat the claim with raw GitHub commands. Continue
   through the [canonical Make lifecycle](../../docs/worktrees.md#normal-agent-workflow).
4. If startup fails or finds no eligible work, stop and report its result. Do not bypass
   it with manual board selection, claims, branch creation or worktree creation. Inspect
   a specific issue only when needed to explain the reported condition or when the human
   explicitly requests board triage or administration.

Report the returned issue, slot and worktree, then work. Repository file edits are
authorized; do not ask again for edit permission. Secret/design-reference restrictions
and human merge gates still apply.
