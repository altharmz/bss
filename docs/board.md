# Task board

How work is tracked on the GitHub Project board. Read this before any board operation
(creating, moving, claiming, closing an item). The session-start protocol ("continue") is
in [.claude/commands/continue.md](../.claude/commands/continue.md).

GitHub Project **BSS** (private, owner `altharmz`, repo `altharmz/bss`). It tracks
**issues**: bugs, human-only tasks, ideas and small tasks. The GUI review and redesign
slices (G4b-G15, R7b-R16) were issues with their own spec and decisions, run in the order
of epic #43's task list (all done; the epic closed 2026-11-05). All planned work lives in
issues; `docs/` holds no backlog or slice tracker (design and decisions only).

**IDs** (looked up 2026-10-28; `gh project field-list 1 --owner altharmz` if they change):

| What | Value |
|------|-------|
| Project number / owner | `1` / `altharmz` |
| Project id | `PVT_kwHOEpM-b84BpHA0` |
| Status field id | `PVTSSF_lAHOEpM-b84BpHA0zhnkE7M` |
| Status option ids | Inbox `f75ad846` · Ready `abe5d139` · Doing `47fc9ee4` · Waiting `98f1210e` · Done `98236657` |

Labels: `ops` `vps` `harness` `docs` `gui` `bug` `idea` `blocked` `vpn-adjacent`
(anything near the VPN rules), `needs-human`, and per-client owner labels such as
`agent:claude`, `agent:codex` and `agent:kimi`. Priority labels: `P0` urgent, `P1` high,
`P2` normal, `P3` low (one per issue).

### Statuses

- **Inbox**: new, not triaged; scope, priority or value still unclear. Never start work
  from Inbox without the human's OK.
- **Ready**: triaged, with a clear goal, acceptance criteria as a task-list checklist,
  labels and no open blockers. Take the next item by priority label: P0 first, then P1,
  P2, P3; an issue with no priority label counts as P3. Among several with the same label, take the one highest on the board.
- **Doing**: actively worked on. An agent may own multiple Doing items when separate
  worker slots are available. It has an `agent:<client>` owner label (or `needs-human`)
  and, for code work, a branch `issue-<n>-<slug>`.
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

### Changing priority with human approval

Changing an existing issue's P0–P3 priority or its manual board order requires explicit
human approval in the conversation for the issue and intended change. A request to
implement work does not itself approve moving it ahead of other issues. Preserve one
priority label per issue and leave other agents' owned work untouched.

Use `gh issue edit <n> --remove-label <old> --add-label <new>` for an approved priority
change. For approved BSS board ordering, the guard permits this narrow inline form:

```sh
gh api graphql -f query='mutation { updateProjectV2ItemPosition(input: {projectId: "PVT_kwHOEpM-b84BpHA0", itemId: "<item id>"}) { clientMutationId } }'
```

Omitting `afterId` moves the item first; add `, afterId: "<preceding item id>"` after
`itemId` to place it after another item. Read item IDs from the board snapshot and
confirm the resulting order afterward. Use `make start-task` to claim/start work.
These board-administration operations have no lifecycle Make target. The guard allows
only this BSS reorder mutation, not arbitrary GraphQL writes or combined mutations.
The shell hook cannot verify conversation approval; agents must enforce that requirement,
as they do the existing human-review rules. There is no approval flag or general bypass.

`make new-priority-task TITLE=... BODY=... [LABELS=...]` creates an explicitly approved
P0 issue first in Ready and stops. It does not claim ownership, check slot availability,
create a worktree or credentials, or start runtime. Start it later through the independent
`make start-task BSS_TASK_OWNER=<client>` selection/claim workflow. A creation failure
after GitHub created the issue identifies its number; inspect it before retrying to
avoid duplicates.

### needs-human items

Never move a `needs-human` item to Doing for yourself. You may prepare it (exact commands,
checklist, expected results) as a comment, then leave it in Ready for the human. When the
human confirms it is done, close it (see Merging and closing).

Board automation (auto-add, item closed, pull request merged) is best-effort; the Make
helpers add an item or set Done themselves when it is late. See
[harness.md](decisions/harness.md#github-board-automation).

### Merging and closing

All work goes on a branch and a PR; the steps are in
[worktrees.md](worktrees.md#normal-agent-workflow). An agent merges its own PR and closes an
issue (`gh issue close`) once it is verified against the acceptance criteria and the board
did not already close it. Never work on an item owned by another agent. CI red: fix it on the
same branch; if you cannot, move the item to Waiting with a `Waiting on:` comment and leave
the PR open.

**Never self-merge without explicit approval** (these wait for the human's review and explicit approval in the
conversation; an earlier approval of the work or the allow list does not count):

- an item, or a PR, labelled `needs-human`;
- a PR that touches a file that grants permissions, runs code or defines policy: `CLAUDE.md`,
  `AGENTS.md`, `.claude/settings.json`, `.claude/hooks/`, `.codex/`, `scripts/pre-commit.sh` or
  `.github/workflows/`. Other `docs/` and `.claude/` files (this file, `.claude/commands/`) are
  not gated.

Approval must name the concrete PR at its exact current head in the current conversation;
new commits require renewed approval. How to pass it:
[worktrees.md](worktrees.md#task-pull-requests-and-readiness).

**Never**, for any PR: `gh pr merge --admin`, merging with failing or pending checks, a
direct push to `master`, a force-push. The allow list cannot check labels or paths, so the
two self-merge exclusions above rest on you; the guard blocks `--admin`, API merges and
force pushes.

**Red master.** If `master` goes red (a push run or the nightly full run), the first step of
the next "continue" is: revert the offending PR (on a branch, as a PR), open an issue for
it, then carry on in the normal order. The revert PR follows the same flow.

**Repo setting.** The repository allows squash merges only and deletes merged branches
(`gh repo edit --enable-squash-merge --enable-merge-commit=false
--enable-rebase-merge=false --delete-branch-on-merge`); this is the human's setting, not an agent's.

### Mechanics

The commands below are for explicitly requested board triage or administration, not normal
startup (`make start-task` claims work). Keep those inspections compact;
fetch only relevant issue bodies using explicit JSON fields (the human-readable view
can query deprecated Projects classic fields and fail):

```
gh project item-list 1 --owner altharmz --limit 100 --format json | jq -r '
  .items | map(select(.status != "Done")) | group_by(.status)[] |
  "\(.[0].status): \(length)",
  (.[] | "  #\(.content.number // "-") [\(.labels | join(","))] \(.title)")'
gh issue view <n> --json number,title,body,labels,comments

gh project item-edit --project-id PVT_kwHOEpM-b84BpHA0 --id <item id> \
  --field-id PVTSSF_lAHOEpM-b84BpHA0zhnkE7M --single-select-option-id <option id>
```

The item id comes from `item-list`. Board workflows do: item added → Inbox, PR linked →
Doing, PR merged / item closed → Done, item reopened → Inbox.
