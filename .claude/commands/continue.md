---
description: Session start - build the picture from git, the board and open PRs, then take the top Ready item
---

Follow this when the human says "continue" or starts a session. Read [docs/board.md](../../docs/board.md)
first for the statuses, ids and `gh` commands used below.

Build the picture before touching code, in this order:

1. Local state: `git status`, current branch, unpushed commits, stashes.
2. **Doing**: resume an item you own (matching branch/PR). More than one, or an item with
   no branch and no recent activity: report it and ask.
3. Open PRs (`gh pr list`): failing checks or requested changes come before new work.
4. **Waiting**: check each "unblocks when". Met: move it to Ready and say so. List the
   still-blocked ones in one line each.
5. **Ready**: take the top item not owned by another agent and not `needs-human`. Add your
   agent label, move it to Doing, create the branch, start.
6. Nothing in Ready: report the Inbox items that need triage and the next slice in
   epic #43's task list, propose what to do, and do not pick work on your own.

Open with a short summary (Doing / Waiting / Ready / Inbox counts, and what you are
taking), then work.
