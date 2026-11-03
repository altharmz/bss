# bss

FastAPI + PostgreSQL BSS on the TMF Open APIs, with a React admin UI. Developed locally
in WSL2, deployed to a remote VPS. Design detail and decisions live in
[`docs/decisions/`](docs/decisions/README.md): read the file for an area before changing
it, and record new decisions there.

## Environments

Local work uses **1 active task = 1 issue = 1 branch = 1 git worktree = 1 worker slot**.
Slots are provider-neutral; a task may span multiple agent sessions. The shared lifecycle
for every client is [worktrees.md](docs/worktrees.md).
When starting new work, run `make start-task` from
the primary checkout. Do not manually select a backlog issue or create its task branch/worktree. Use exactly the returned
issue, branch, selected slot and worktree. Do not guess which slot is free.
If it fails, stop and report the failure instead of bypassing
it. A concrete human request still takes precedence over unrelated Ready work.
Strongly prefer an explicit client identity: `make start-task BSS_TASK_OWNER=codex`
for Codex, or `make start-task BSS_TASK_OWNER=claude` for Claude. Do not manually inspect
the board or enumerate statuses and open PRs before startup; the helper selects and
claims eligible work. Inspect the returned issue for implementation context afterward.

The primary checkout stays clean on `master` for administration only; implementation,
commits, runtime and generated artifacts belong in linked task worktrees. Never switch
branches inside a task worktree or mutate sibling worktrees. All local Compose operations
go through `scripts/task-compose` (the Make targets use it). Set explicit ports in the
ignored `.task/runtime.json`; development secrets live outside worktrees at
`~/.config/bss/tasks/<task_id>/.env`. Setup and schema: [worktrees.md](docs/worktrees.md).

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM) | the human's deployed instance; not part of development |
| Cloud session (claude.ai/code) | branches only, no Docker, no VPS: [docs/cloud.md](docs/cloud.md) |

## Commands

```
make up / down / logs / ps       # local stack (app + postgres, hot reload)
make test                       # FULL suite in the container
make lint / fmt                 # ruff check / auto-format
make scripts-lint / scripts-fmt # changed Python harness scripts; optional SCRIPT_FILES=JSON-list
make migrate                    # alembic upgrade head locally
make worker-once                # one billing-worker wake-up
make seed                       # local demo data (refuses prod)
make psql                       # local db (needs a TTY)
make smoke / mcp-smoke          # local app / MCP connector checks
make check                      # on demand: lint, full test, docs, UI and browser checks
make test-smoke / test-changed  # smoke subset / affected tests (the hook runs both)
make docs                       # regenerate docs/api + docs/db
make progress                   # docs/PROGRESS.md (slow, on request)
make hooks                      # install pre-commit and pre-push hooks
make mcp-token-create NAME= SCOPE= / mcp-tokens / mcp-token-revoke ID=
make web-install                # npm ci (the only step that downloads)
make web-dev / web-build / web-lint / web-test / web-types / web-fmt / web-e2e / web-shots
```

CI and the hook's test selection: [harness.md](docs/decisions/harness.md#ci-2026-10-28).

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

Standing human approval, reaffirmed 2026-11-02: edits to all repository files are authorized,
including instruction/workflow files and linked task worktrees, across future sessions.
Never ask again for repository edit consent. Existing secret/design-reference restrictions
and human merge gates still apply. Sandbox approval requirements remain separate.

Standing authorization: adding relevant task and PR progress, validation, blocker and
telemetry comments is authorized. Never ask for permission or confirmation to add these
comments. Prefer Make helpers where available. Required tool sandbox approvals remain
separate.

Follow the [canonical lifecycle](docs/worktrees.md#normal-agent-workflow) and
[shared AI guidance](docs/worktrees.md#ai-workflow-guidance) for tool choice, engineering
review, recovery, long-running checks, human gates and completion reporting. Use
repository Make targets wherever they cover the operation, including read-only task
review and PR readiness. Use ordinary commands for source investigation, source editing
and details the targets do not expose; see the shared tool-choice guidance. The shared guidance defines the
recovery order when a helper cannot represent a required safe correction.

- **One development cycle: develop -> commit -> push, with one local test run.**
  The pre-commit hook runs `make lint`, the smoke tests and the tests the change affects
  (pytest-testmon), **not the full suite** (+ `web-lint`/`web-test` when `frontend/` or
  `spec/` is staged, `docs-check` when the generated docs' sources are; nothing for a
  Markdown-only commit) and is that run. Commit and push when you judge the change
  ready - no approval needed. **Before opening a PR that touches billing, migrations or
  the API, run `make test` (the full suite, ~7 min) locally**; CI runs it nightly.
- **Branches:** all work goes on a branch and a PR with `Fixes #N`, never a direct push to
  `master`. Agents merge their own PRs when CI is green, except the cases in
  [board.md](docs/board.md#merging-and-closing).
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

Codex, Claude and Kimi save machine-observed usage statistics in the current issue's
comments at two checkpoints: the beginning of work and PR creation. Make posts these
through `start-task` and `create-pr`; verify the comments. Retry a failed checkpoint with
`make telemetry-comment PHASE=start` or `make telemetry-comment PHASE=pr`. Show the
starting/latest counters and observed difference, client/model/session participation,
estimated cost with provenance when supported, and explicit missing/partial data.
Never guess counts or present unknown usage/cost as zero. On resume/handoff run
`make telemetry-collect`; source/session setup is in [telemetry-usage.md](docs/telemetry-usage.md).
Kimi agents must always use `make telemetry-collect CLIENT=kimi SESSION_ID=<actual-session-uuid>`
with the actual session ID, including on resume and handoff.

`make new-priority-task` creates an explicitly approved P0 issue first in Ready, then stops.
Run `make start-task` separately to select, claim and prepare work.

Tasks live on the GitHub Project **BSS** (owner `altharmz`) as issues; planned code slices
stay in the slice trackers in `docs/` and an issue only links to them. Rules in short:

- Read [docs/board.md](docs/board.md) before any board operation (statuses, ids, `gh`
  commands, merge/close rules).
- Statuses: Inbox, Ready, Doing, Waiting, Done. `make start-task` selects from Ready by priority label (P0-P3), then board order among equals; do not select manually.
- Concurrency is limited only by available worker slots, not by per-agent WIP. Startup
  claims an item with your `agent:<client>` label (for example `agent:claude`,
  `agent:codex` or `agent:kimi`) and moves it to Doing in one step; never touch another
  agent's items.
- Merging: use `make create-pr` (adds `Fixes #N`), observe `make check-pr`, and merge with
  `make merge-task` only when ready. After explicit human approval of the concrete PR and
  exact current head in this conversation, pass
  `HUMAN_APPROVAL=<PR-number>:<full-head-SHA>` to `make merge-task`; new commits require
  renewed approval and all other gates still apply. Without that approval never self-merge a
  `needs-human` item or a PR touching CLAUDE.md, AGENTS.md, docs/board.md, `.claude/`,
  `scripts/pre-commit.sh` or `.github/workflows/`; never use `--admin`, merge on failing
  or pending checks, or push to `master`. Details: board.md.
- Never start work from Inbox without the human's OK; never move a `needs-human` item
  to Doing for yourself.
- On "continue", or at session start without a concrete request, follow
  [.claude/commands/continue.md](.claude/commands/continue.md). A concrete request takes
  precedence: inspect only the state it needs and do not take unrelated Ready work.

## Current state

- **Built:** the TMF APIs with the billing worker, the MCP connector, the prepaid line
  (P1-P7, [decision](docs/decisions/prepaid.md)), subscriber numbers
  ([release evidence](docs/subscriber-number-release.md)), every MCP operation in the UI
  with the UI/MCP parity check, and the Ledger v2 redesign.
- **Planned code work:** the GUI fix slices G4b-G15 interleaved with redesign slices
  R7b-R16 are board issues, listed in run order in epic #43 (rules, orchestrator and subagent
  prompt are in the epic). Specs: [ui-redesign-design.md](docs/ui-redesign-design.md),
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
