# Task board

How work is tracked on the GitHub Project board. Read this before any board operation
(creating, moving, claiming, closing an item). The session-start protocol ("continue") is
in [.claude/commands/continue.md](../.claude/commands/continue.md).

GitHub Project **BSS** (private, owner `altharmz`, repo `altharmz/bss`). It tracks
**issues**: bugs, human-only tasks, ideas and small tasks. The GUI review and redesign
slices (G4b-G15, R7b-R16) are issues carrying their own spec and decisions, ordered by the
task list of epic #43. The older slice trackers in `docs/` ([next-slices.md](next-slices.md),
[prepaid-slices.md](prepaid-slices.md)) stay the source of truth for their slices: an issue
for one of those only links to it.

**IDs** (looked up 2026-10-28; `gh project field-list 1 --owner altharmz` if they change):

| What | Value |
|------|-------|
| Project number / owner | `1` / `altharmz` |
| Project id | `PVT_kwHOEpM-b84BpHA0` |
| Status field id | `PVTSSF_lAHOEpM-b84BpHA0zhnkE7M` |
| Status option ids | Inbox `f75ad846` · Ready `abe5d139` · Doing `47fc9ee4` · Waiting `98f1210e` · Done `98236657` |

Labels: `ops` `vps` `harness` `docs` `gui` `bug` `idea` `blocked` `vpn-adjacent`
(anything near the VPN rules), `needs-human`, and the owner labels `agent:claude` and
`agent:codex`.

### Statuses

- **Inbox**: new, not triaged; scope, priority or value still unclear. Never start work
  from Inbox without the human's OK.
- **Ready**: triaged, with a clear goal, acceptance criteria as a task-list checklist,
  labels and no open blockers. Board order is priority, top first.
- **Doing**: actively worked on. WIP limit: 1 item per agent. It has an owner label
  (`agent:claude`, `agent:codex` or `needs-human`) and, for code work, a branch
  `issue-<n>-<slug>` (cloud sessions: `cloud/issue-<n>-<slug>`, the only pushable form).
- **Waiting**: blocked by something outside the agent's control (another
  issue, a VPS step, the human's decision). Every move to Waiting gets a comment
  `Waiting on: <what> — unblocks when: <condition>`.
- **Done**: finished and verified against the acceptance criteria. Normally set by the
  board workflows when a PR with `Fixes #N` is merged or the issue is closed. Close an
  issue yourself only when it is verified against its acceptance criteria and the board
  did not already do it (see Merging and closing).

### Adding a task ("add a task / bug / idea")

- Search first (`gh issue list --search`); if one exists, comment on it instead.
- Create the issue with a clear title, short context, a task-list checklist and labels.
  It lands in Inbox by default.
- If the human gave enough detail and said it should be done soon, put it straight into
  Ready and say where you placed it.

### Planning ("plan / prepare X")

- Refine the issue: goal, scope, acceptance criteria checklist, dependencies, labels;
  split into sub-issues if it is more than one session of work.
- An open dependency: move it to Waiting with the "Waiting on" comment. Otherwise move it
  to Ready and ask where it sits in priority. Do not start implementing unless told to.

### needs-human items

Never move a `needs-human` item to Doing for yourself. You may prepare it (exact commands,
checklist, expected results) as a comment, then leave it in Ready for the human. When the
human confirms it is done, close it (see Merging and closing).

### Parallel agents

Claude Code and Codex may run at the same time. Before taking a Ready item check it has no
agent label; claim it by adding yours and moving it to Doing in one step. Never work on an
item owned by the other agent.

### Merging and closing

An agent may merge a PR (`gh pr merge`) and close an issue (`gh issue close`) once all
checks pass and the work is verified against the acceptance criteria. If the PR, or the
issue it fixes, is labelled `needs-human`, do it **only after the human's explicit approval
in the conversation** - an earlier approval of the work, or the allow list, does not
count. The allow list cannot check labels, so this rule is the only guard.

### Mechanics

```
gh project item-list 1 --owner altharmz --format json      # items with Status, labels, number
gh project item-edit --project-id PVT_kwHOEpM-b84BpHA0 --id <item id> \
  --field-id PVTSSF_lAHOEpM-b84BpHA0zhnkE7M --single-select-option-id <option id>
```

The item id comes from `item-list`. Board workflows do: item added → Inbox, PR linked →
Doing, PR merged / item closed → Done, item reopened → Inbox.
