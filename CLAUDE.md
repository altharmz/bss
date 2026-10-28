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

Tasks live on the GitHub Project **BSS** (owner `altharmz`) as issues; planned code slices
stay in the slice trackers in `docs/` and an issue only links to them. Rules in short:

- Read [docs/board.md](docs/board.md) before any board operation (statuses, ids, `gh`
  commands, merge/close rules).
- Statuses: Inbox, Ready, Doing, Waiting, Done. Board order in Ready is priority.
- WIP limit: 1 item per agent. Claim an item with your label (`agent:claude` /
  `agent:codex`) and move it to Doing in one step; never touch the other agent's items.
- Never start work from Inbox without the human's OK; never move a `needs-human` item
  to Doing for yourself.
- On "continue" or at session start, follow [.claude/commands/continue.md](.claude/commands/continue.md).

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
