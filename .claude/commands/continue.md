---
description: Session start - resume existing work or start the next task with make start-task
---

Follow this when the human says "continue" or starts a session without a concrete task. Read
[docs/board.md](../../docs/board.md) first for the statuses, ids and `gh` commands used below.
A concrete request takes precedence: check its local state and only the board or PR state it
needs. Do not run the full startup sequence or take unrelated Ready work.

For the full protocol, build the picture before touching code. **First, check `master`**:
`gh run list --branch master --limit 5`. If the latest push run or the nightly full run failed,
revert the offending PR (a branch and a PR), open an issue for it, then carry on - see board.md
"Red master". Limit board inspection to owned Doing items and Waiting unblock conditions; do
not fetch or rank the Ready list. Fetch a full issue body only for an existing item being
resumed or the issue returned by startup.

Then, in this order:

1. Locate existing work with only the read-only inspection needed. In an identified task
   checkout, use `make review-task` for routine local-state checks. Use direct Git commands
   for primary/task discovery or details it does not expose (such as unpushed commits or
   stashes), when relevant; do not run a blanket Git inventory before every Make target.
2. **Doing**: resume an item you own (matching branch/PR). More than one, or an item with
   no branch and no recent activity: report it and ask.
3. Discover open PRs with `gh pr list`; for an identified task PR, use `make check-pr`
   from its task checkout. Failing checks or requested changes come before new work.
4. **Waiting**: check each "unblocks when". Met: move it to Ready and say so. List the
   still-blocked ones in one line each.
5. **Ready**: run `make start-task` immediately from clean primary master, following
   CLAUDE.md. Do not manually list, compare, rank or select Ready issues; the helper is the
   only Ready-selection mechanism. Use exactly the returned issue, branch, slot and worktree.
   The helper claims the agent label and Doing status as part of startup; do not repeat the
   claim with raw GitHub commands. Continue through the
   [canonical Make lifecycle](../../docs/worktrees.md#normal-agent-workflow).
6. **No eligible Ready item**: if startup reports none, inspect the board and report the Inbox
   items that need triage and the next slice in epic #43's task list. Propose what to do and
   do not pick work on your own.

Open with a short summary of existing work and what you are starting, then work.
