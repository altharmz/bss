# bss

FastAPI + PostgreSQL BSS on the TMF Open APIs, with a React admin UI. Developed locally
in WSL2; the human deploys to a remote VPS. Design detail and decisions live in
[`docs/decisions/`](docs/decisions/README.md): read the file for an area before changing
it, and record new decisions there.

## How to work

Every regular operation has a `make` target. Use it, and run `make help` to discover
others. If a target fails, read its error and fix the cause; fall back to a plain command
only when no target covers the step, and say so in your summary.

**1 task = 1 issue = 1 branch = 1 git worktree = 1 worker slot.** The full workflow, with a
table of operations and their targets, is
[docs/worktrees.md](docs/worktrees.md#normal-agent-workflow). In short:

1. From the clean primary checkout: `make start-task BSS_TASK_OWNER=<client>` (`claude`,
   `codex`, `kimi`). It selects the issue and a free slot and claims them; use exactly
   what it returns. Do not pick or claim board items yourself. If it fails, stop and report.
   Once it succeeds, the claimed task is approved work: report the issue, slot and worktree
   in one line and carry on through delivery without asking whether to start.
2. In the returned worktree: implement, `make format`, `make review-task`.
3. `make commit MESSAGE="..." FILES='["path", ...]'` (the pre-commit hook is the one local
   test run), then `make create-pr`, `make check-pr`, `make merge-task`.
4. After the merge, from the primary checkout: `make finish-task SLOT=<slot>`.

The primary checkout stays clean on `master` and is for administration only. Work, commits
and runtime belong in the task worktree. Development secrets live outside the worktree
(`~/.config/bss/tasks/<task_id>/.env`); ports are in the ignored `.task/runtime.json`.

On "continue", or at session start without a request, follow
[.claude/commands/continue.md](.claude/commands/continue.md). A concrete request takes
precedence. Board rules: [docs/board.md](docs/board.md). Do not start work from Inbox or
take a `needs-human` item without the human's OK.

Standing authorizations: repository edits (including instruction files), issue comments,
commits and pushes on task branches need no approval. Merging a PR that touches
`needs-human`, CLAUDE.md, AGENTS.md, `.claude/settings.json`, `.claude/hooks/`, `.codex/`,
`scripts/pre-commit.sh` or `.github/workflows/` needs the human's explicit approval of
that PR at its exact head (`make merge-task HUMAN_APPROVAL=<PR>:<head-SHA>`).

## Commands

```
make up / down / logs / ps / psql   # local stack (app + postgres, hot reload)
make test                           # FULL suite in the container (~11 min)
make lint / fmt / format            # ruff check / auto-format / backend + UI
make migrate / seed / worker-once   # alembic upgrade, demo data, one billing wake-up
make smoke / mcp-smoke              # local app / MCP connector checks
make check                          # optional: lint, full test, docs, UI and browser checks
make docs                           # regenerate docs/api + docs/db
make web-install                    # npm ci, the only step that downloads
make web-lint / web-test / web-build / web-types / web-fmt / web-e2e
make scripts-lint / scripts-fmt     # Python harness scripts
make runtime-check                  # harness script tests (~1 min, parallel)
make deploy-check                   # deploy script tests (~1 min)
make docs-links                     # Markdown links and the commands the docs name
make telemetry-collect / telemetry-report / telemetry-summary
```

## Rules

- **Tests:** one local run per cycle - the pre-commit hook (lint, smoke tests, the tests the
  change affects). Run `make test` (the full suite) before a PR that touches billing,
  migrations or the API. Every new feature or fix comes with a test that fails without it;
  never delete, skip or weaken a test to make the suite pass. If a test is wrong because
  the requirements changed, change it and add `TEST-CHANGE: <reason>` to the commit message.
- **Git:** work on the task branch and a PR with `Fixes #N`; never push to `master`, force-push
  or rewrite remote history, or skip the hooks. Commit one finished slice at a time (code,
  tests, migration and generated docs together).
- **Generated docs:** with a migration or API change run `make docs` and commit `docs/api`
  and `docs/db` (the hook refuses stale ones). Add new tables, operations and screens to
  `docs/progress/scope.yaml`.
- **Secrets:** never read, print or commit `.env`, `.env.prod` or `.env.smoke` (use
  `.env.example`); no `docker compose config` without `--quiet`. A production MCP token is
  created by the human; never add an unauthenticated MCP mode.
- **Docker:** only `make` and `scripts/task-compose` touch the local stack; never prune or
  remove containers, volumes or networks you did not start - other projects share the host.
- **Migrations** already on `master` are immutable.
- **Deployment is the human's** (`make deploy` and friends). Your work ends at the merge to
  `master`; do not run, track or suggest deploys. The VPS also runs a production VPN server
  that is off limits.
- Propose a plan before a data-model change or a large design change; otherwise act. When a
  container misbehaves, read `make logs` before changing code. If the guard
  (`.claude/hooks/guard.py`) blocks something you believe is legitimate, say so.

**Code rules** (details in the linked files): money is `Decimal`, never float; nothing in
`app/` reads the wall clock except `app/clock.py`; ranges are half-open and billing follows
dates, not status ([data-model.md](docs/decisions/data-model.md)). Dependencies are pinned;
`/healthz` stays cheap and checks the DB ([infra.md](docs/decisions/infra.md#conventions)).
Unknown attributes are a 400; the contract is the vendored specs in `spec/`, never
`/openapi.json`; implement what the specs define, skip the rest
([tmf-api.md](docs/decisions/tmf-api.md)). Orders alone create or end products; only the
billing run creates ABRs and bills. Node runs only in containers; the UI's write requests
are pinned on both sides ([frontend.md](docs/decisions/frontend.md#frontend-conventions)).

## Usage telemetry

Task usage (tokens per client, model and session) is recorded in a local telemetry database
for later analysis. `make start-task` and `make finish-task` record it; on resume or hand-off
run `make telemetry-collect` (Kimi: `make telemetry-collect CLIENT=kimi SESSION_ID=<uuid>`).
Unknown values stay unknown, never zero. Setup and reporting: [telemetry-usage.md](docs/telemetry-usage.md).

## Current state

- **Built:** the TMF APIs with the billing worker, the MCP connector, the prepaid line,
  subscriber numbers, every MCP operation in the UI with the UI/MCP parity check, the GUI
  review fixes and the Ledger v2 redesign ([release evidence](docs/ui-redesign-release.md)).
- **Planned code work** is board issues, listed in run order in epic #43 (rules and specs: the epic
  and slice issues, [patterns.md](docs/patterns.md); the
  handoff `design-ref/design_handoff_bss_admin_v2/` is read-only). Everything else open
  (bugs, human-only checks, ideas) is a board issue. History is `git log` and closed PRs.

## Where the decisions are

[data-model](docs/decisions/data-model.md) · [tmf-api](docs/decisions/tmf-api.md) ·
[catalog](docs/decisions/catalog.md) · [accounts](docs/decisions/accounts.md) ·
[events](docs/decisions/events.md) · [billing-core](docs/decisions/billing-core.md) ·
[charge-periods](docs/decisions/charge-periods.md) · [prepaid](docs/decisions/prepaid.md) ·
[worker](docs/decisions/worker.md) · [bills](docs/decisions/bills.md) ·
[billing-completion](docs/decisions/billing-completion.md) ·
[internal-api](docs/decisions/internal-api.md) · [mcp](docs/decisions/mcp.md) ·
[users](docs/decisions/users.md) · [frontend](docs/decisions/frontend.md) ·
[infra](docs/decisions/infra.md) · [harness](docs/decisions/harness.md) ·
[tracking](docs/decisions/tracking.md)
