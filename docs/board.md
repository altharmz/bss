# Task board

How work is tracked on the GitHub Project board. Read this before any board operation
(creating, moving, claiming, closing an item). The session-start protocol ("continue") is
in [.claude/commands/continue.md](../.claude/commands/continue.md).

GitHub Project **BSS** (private, owner `altharmz`, repo `altharmz/bss`). It tracks
**issues**: bugs, human-only tasks, ideas and small tasks. The GUI review and redesign
slices (G4b-G15, R7b-R16) are issues carrying their own spec and decisions, ordered by the
task list of epic #43. All planned work lives in issues; `docs/` holds no backlog or
slice tracker (design and decisions only).

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
  P2, P3. Among several with the same label, take the one highest on the board.
- **Doing**: actively worked on. An agent may own multiple Doing items when separate
  worker slots are available. It has an `agent:<client>` owner label (or `needs-human`)
  and, for code work, a branch `issue-<n>-<slug>` (cloud sessions:
  `cloud/issue-<n>-<slug>`, the only pushable form).
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

### Parallel agents

Claude Code and Codex may run at the same time, and one agent may run multiple tasks in
separate slots. Use `make start-task` to select and claim work; it checks eligibility,
ownership and slot availability before moving the item to Doing. Strongly prefer
`make start-task BSS_TASK_OWNER=codex` for Codex. Do not manually inspect or claim Ready
items before startup. Never work on an item owned by the other agent.

### Merging and closing

All work goes on a branch and a PR; nothing is pushed to `master` directly. An agent merges
its own PR, and closes an issue (`gh issue close`) once it is verified against the
acceptance criteria and the board did not already close it.

**Flow**

1. Use `make create-pr` from the task checkout; it adds `Fixes #N` and publishes safely.
2. Before a PR that touches billing, migrations or the API, run `make test` (the full
   suite) locally.
3. Observe `make check-pr`; repeat while pending (`gh pr checks --watch` is also read-only).
4. Every check green and policy ready: `make merge-task`. A human-review gate stays
   blocked until explicit conversation approval is supplied through
   `make merge-task HUMAN_APPROVAL=<PR-number>:<full-head-SHA>`; never bypass it with a raw
   merge command. See [approval input](worktrees.md#task-pull-requests-and-readiness).
5. CI red: fix it on the same branch and push. If you cannot, move the item to Waiting with
   a comment (`Waiting on: <what> — unblocks when: <condition>`) and leave the PR open.

**Never self-merge without explicit approval** (these wait for the human's review and explicit approval in the
conversation; an earlier approval of the work or the allow list does not count):

- an item, or a PR, labelled `needs-human`;
- a PR that touches `CLAUDE.md`, `AGENTS.md`, `docs/board.md`, `.claude/` (settings, hooks,
  commands), the guard and its cases (including `scripts/workflow_guard.py` and
  `scripts/test_workflow_guard.py`), `scripts/pre-commit.sh` or `.github/workflows/`.

Approval must name the concrete PR at its exact current head in the current conversation.
Only then may the agent pass `HUMAN_APPROVAL=<PR-number>:<full-head-SHA>` to
`make merge-task`. New commits require renewed approval. All other readiness gates and
GitHub protections remain enforced; see the canonical approval input documentation above.

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

Each agent saves machine usage statistics in comments on its current issue at the
beginning of work and when the PR is created. `make start-task` and `make create-pr`
post the two checkpoints; retry failures with `make telemetry-comment PHASE=start|pr`.
The PR comment shows starting/latest counters and their observed difference, client/
model/session participation, supported estimated costs with provenance, and explicit
coverage/pricing gaps. Never fill unknown counters or costs with zeroes or estimates
from conversation text. See [source setup and recovery](telemetry-usage.md).

Normal task startup uses `make start-task`, strongly preferably
`make start-task BSS_TASK_OWNER=codex` for Codex. Do not run a manual board snapshot,
status count, candidate selection or claim before it. Fetch the returned issue's body
for implementation context afterward.

The commands below are for explicitly requested board triage/administration or diagnosis
of a specific reported condition, not normal startup. Keep those inspections compact;
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
