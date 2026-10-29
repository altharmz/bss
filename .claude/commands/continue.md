---
description: Session start - build the picture from git, the board and open PRs, then take the highest-priority Ready item
---

Follow this when the human says "continue" or starts a session without a concrete task. Read
[docs/board.md](../../docs/board.md) first for the statuses, ids and `gh` commands used below.
A concrete request takes precedence: check its local state and only the board or PR state it
needs. Do not run the full startup sequence or take unrelated Ready work.

For the full protocol, build the picture before touching code. **First, check `master`**:
`gh run list --branch master --limit 5`. If the latest push run or the nightly full run failed,
revert the offending PR (a branch and a PR), open an issue for it, then carry on - see board.md
"Red master". Use the compact board snapshot in board.md "Mechanics"; fetch the full body only
for the issue being considered.

Then, in this order:

1. Local state: `git status`, current branch, unpushed commits, stashes.
2. **Doing**: resume an item you own (matching branch/PR). More than one, or an item with
   no branch and no recent activity: report it and ask.
3. Open PRs (`gh pr list`): failing checks or requested changes come before new work.
4. **Waiting**: check each "unblocks when". Met: move it to Ready and say so. List the
   still-blocked ones in one line each.
5. **Ready**: take an item with the highest priority label (P0, then P1, P2, P3) that is
   not owned by another agent and not `needs-human`; if several share that label, take the one highest on the board. Add your
   agent label, move it to Doing, create the branch, start.
6. Nothing in Ready: report the Inbox items that need triage and the next slice in
   epic #43's task list, propose what to do, and do not pick work on your own.

Open with a short summary (Doing / Waiting / Ready / Inbox counts, and what you are
taking), then work.
