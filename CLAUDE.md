# bss

FastAPI + PostgreSQL BSS on the TMF Open APIs, with a React admin UI. Developed locally
in WSL2, deployed to a remote VPS. Design detail and decisions live in
[`docs/decisions/`](docs/decisions/README.md): read the file for an area before changing
it, and record new decisions there.

## Environments

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM) | the human's deployed instance; not part of development |
| Cloud session (claude.ai/code) | branches only, no Docker, no VPS: [docs/cloud.md](docs/cloud.md) |

## Commands

```
make up / down / logs / ps   # local stack (app + postgres, hot reload)
make test      # pytest in the container      make lint / fmt   # ruff check / auto-format
make migrate   # alembic upgrade head locally make worker-once   # one billing-worker wake-up
make seed      # local demo data (refuses prod) make psql        # local db (needs a TTY)
make smoke     # smoke-test the local instance make mcp-smoke    # local MCP connector checks
make check     # on demand only: lint, test (ci profile), docs-check, web-lint/test/e2e
make docs      # regenerate docs/api + docs/db  make progress    # docs/PROGRESS.md (slow, on request)
make hooks     # install pre-commit (lint + test) and pre-push (secrets) hooks
make mcp-token-create NAME= SCOPE= / mcp-tokens / mcp-token-revoke ID=   # local MCP tokens
make web-install   # npm ci (the only step that downloads)
make web-dev / web-build / web-lint / web-test / web-types / web-fmt / web-e2e / web-shots
```

The VPS and deploying (`make deploy`, `remote`, `ssh`, `tunnel`, `deployed`) are the human's
tools, not part of development: see Working style.

## Hard constraints

- The VPS also runs a production **SoftEther VPN server**. Ports **443, 992, 1194, 500,
  4500, 5555, 8444** are off limits; never touch, inspect or reconfigure anything VPN-related.
- **Never `docker build` on the VPS** (one core, the VPN next to us). Images are built
  locally and shipped by `make deploy`; the VPS is changed only through `scripts/deploy.sh`
  (`make deploy`, `make remote`).
- Never run `docker system/volume/network/builder prune`, `docker volume rm`, or stop or
  remove containers you did not start - other projects share both hosts.
- On the VPS the app binds to `127.0.0.1:18081` only; nothing is published to 0.0.0.0.
  The project lives in `~/bss`; no other project may share that directory.
- No `db` or `caddy` service in `compose.prod.yaml`, no Caddyfile or backup scripts here:
  they belong to the separate vps-infra project ([infra.md](docs/decisions/infra.md#shared-infrastructure-vps-infra)).
- **Secrets:** never read, print or commit `.env` / `.env.prod` / `.env.smoke` (use
  `.env.example`); no `docker compose config` without `--quiet`, no `run/exec ... env`.
  Never export `.env.prod` into a shell that also runs the local stack. A production MCP
  token is created by the human, not a session; never add an unauthenticated MCP mode.
- Migrations already on `master` are immutable.
- The VPS reboots nightly at 03:00: everything must come back on its own.
- Git: never force-push, delete remote branches or rewrite remote history; never skip
  the hooks (`--no-verify`). Cloud sessions push only `git push -u origin cloud/<topic>`.

The guard (`.claude/hooks/guard.sh`, cases in `guard-cases.txt`) blocks the obvious
slips against these rules; it is a pattern matcher, not a security boundary
([harness.md](docs/decisions/harness.md)).

## Working style

- **One development cycle: develop -> commit -> push, with one local test run.**
  The pre-commit hook runs `make lint` + `make test` (+ `web-lint`/`web-test` when
  `frontend/` or `spec/` is staged, `docs-check` when the generated docs' sources are;
  nothing for a Markdown-only commit) and is that run. Commit and push when you judge
  the change ready - no approval needed.
- **Commit granularity:** one commit per finished slice (code, tests, migration,
  generated docs together). Fold status, hand-off and process/CLAUDE.md edits into
  the next slice commit or one batched commit; amend or squash unpushed commits
  instead of stacking fix-ups (pushed history stays untouched).
- **Deployment is the human's, outside development: your work ends at the merge to
  master.** Never run `make deploy`/`verify`/`remote`/`ssh`/`tunnel` or `/ship`, never
  track, ask about or suggest a deploy, and never write `.deployed` or record a deployment
  in the docs. Nothing beyond the hook is required: targeted tests while developing, `make check`,
  `make web-e2e`, `make progress` or a negative proof are optional - run one when you
  judge it worth its time. This section overrides process steps in older plans and
  hand-offs (separate commit approvals, three backend passes, release proofs, deploy steps).
- Every slice adds new tests; every new feature or bug fix comes with a test that fails
  without it. Never delete, skip or weaken an existing test to make the suite pass - fix
  the code. If a test is genuinely wrong because the requirements changed, modify it and
  add a line `TEST-CHANGE: <reason>` to the commit message. To show a new test fails
  without its change (optional), run only that test - `docker compose run --rm app pytest tests/<file>.py
  -k <name>` before the fix or with the change reverted - never a full suite.
- Propose a plan before a data-model change or a large design change; otherwise act.
- When a container misbehaves, read `make logs` before changing code.
- If the guard blocks something you believe is legitimate, say so instead of working
  around it.
- With a migration or API change, run `make docs` and commit `docs/api`/`docs/db` with it
  (the hook refuses stale ones). Add new tables/operations/screens to
  `docs/progress/scope.yaml`; `make progress` (slow, whole suite) runs only on request.

**Code rules** (full text in the linked files): money is `Decimal`, never float; nothing
in `app/` reads the wall clock except `app/clock.py`; ranges are half-open and billing
follows dates, not status ([data-model.md](docs/decisions/data-model.md)). Dependencies
are pinned; `/healthz` stays cheap and checks the DB ([infra.md](docs/decisions/infra.md#conventions)).
Unknown attributes are a 400; the contract is the vendored specs in `spec/`, never
`/openapi.json`; follow TMF: implement what the vendored specs define, skip the rest
([tmf-api.md](docs/decisions/tmf-api.md)). Orders alone create or end products; only the
billing run creates ABRs and bills. Node runs only in containers on the workstation; the
UI's write requests are pinned on both sides ([frontend.md](docs/decisions/frontend.md#frontend-conventions)).

## Task board

GitHub Project **BSS** (private, owner `altharmz`, repo `altharmz/bss`). It tracks
**issues**: bugs, human-only tasks, ideas and small tasks. The slice trackers in `docs/`
([next-slices.md](docs/next-slices.md), [gui-review-plan.md](docs/gui-review-plan.md),
[prepaid-slices.md](docs/prepaid-slices.md)) stay the source of truth for planned code
work: a code slice lives there, and an issue for it only links to it. Never copy a spec
into an issue.

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

### Session start / "continue"

Build the picture before touching code, in this order:

1. Local state: `git status`, current branch, unpushed commits, stashes.
2. **Doing**: resume an item you own (matching branch/PR). More than one, or an item with
   no branch and no recent activity: report it and ask.
3. Open PRs (`gh pr list`): failing checks or requested changes come before new work.
4. **Waiting**: check each "unblocks when". Met: move it to Ready and say so. List the
   still-blocked ones in one line each.
5. **Ready**: take the top item not owned by another agent and not `needs-human`. Add your
   agent label, move it to Doing, create the branch, start.
6. Nothing in Ready: report the Inbox items that need triage and the next item of
   [next-slices.md](docs/next-slices.md), propose what to do, and do not pick work on your own.

Open with a short summary (Doing / Waiting / Ready / Inbox counts, and what you are
taking), then work.

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

## Current state

- **Built:** the TMF APIs with the billing worker, the MCP connector, the prepaid line
  (P1-P7, [decision](docs/decisions/prepaid.md)), subscriber numbers
  ([release evidence](docs/subscriber-number-release.md)), every MCP operation in the UI
  with the UI/MCP parity check, and the Ledger v2 redesign.
- **Planned code work** lives in the slice trackers: [next-slices.md](docs/next-slices.md),
  [gui-review-plan.md](docs/gui-review-plan.md) (fix slices G1-G15 interleaved with redesign
  slices R7b-R16; run it as the orchestrator its "Orchestrator" section describes),
  [prepaid-slices.md](docs/prepaid-slices.md). Specs: [ui-redesign-design.md](docs/ui-redesign-design.md),
  [patterns.md](docs/patterns.md); the handoff `design-ref/design_handoff_bss_admin_v2/` is read-only.
- **Everything else open** (bugs, human-only checks, ideas) is a board issue. Changing
  `billCycleDay` stays out (PATCH is a 409). History is `git log` and the closed PRs.

## Where the decisions are

[data-model](docs/decisions/data-model.md) · [tmf-api](docs/decisions/tmf-api.md) ·
[catalog](docs/decisions/catalog.md) · [accounts](docs/decisions/accounts.md) ·
[events](docs/decisions/events.md) · [billing-core](docs/decisions/billing-core.md) ·
[charge-periods](docs/decisions/charge-periods.md) · [prepaid](docs/decisions/prepaid.md) ·
[worker](docs/decisions/worker.md) · [bills](docs/decisions/bills.md) ·
[billing-completion](docs/decisions/billing-completion.md) ·
[internal-api](docs/decisions/internal-api.md) · [mcp](docs/decisions/mcp.md) ·
[users](docs/decisions/users.md) · [frontend](docs/decisions/frontend.md) ·
[infra](docs/decisions/infra.md) · [harness](docs/decisions/harness.md)
