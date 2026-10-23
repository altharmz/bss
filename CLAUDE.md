# bss

FastAPI + PostgreSQL BSS on the TMF Open APIs, with a React admin UI. Developed locally
in WSL2, deployed to a remote VPS. Design detail and decisions live in
[`docs/decisions/`](docs/decisions/README.md): read the file for an area before changing
it, and record new decisions there. Status history: [docs/status.md](docs/status.md).

## Environments

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM) | the deployed instance - pre-built image, no build |
| Cloud session (claude.ai/code) | branches only, no Docker, no VPS: [docs/cloud.md](docs/cloud.md) |

## Commands

```
make up / down / logs / ps   # local stack (app + postgres, hot reload)
make test      # pytest in the container      make lint / fmt   # ruff check / auto-format
make migrate   # alembic upgrade head locally make worker-once   # one billing-worker wake-up
make seed      # local demo data (refuses prod) make psql        # local db (needs a TTY)
make smoke     # smoke-test the local instance make mcp-smoke    # local MCP connector checks
make deploy    # build locally, ship to the VPS, health + remote/web/MCP smoke
make verify    # = make deploy (the commit was already tested by the pre-commit hook)
make deployed  # last deploy (.deployed, written only by deploy.sh) + commits not deployed yet
make check     # on demand only: lint, test (ci profile), docs-check, web-lint/test/e2e
make docs      # regenerate docs/api + docs/db  make progress    # docs/PROGRESS.md (slow, on request)
make remote CMD="..." [SERVICE=mcp]   # one-off command on the VPS image
make ssh / tunnel                     # VPS shell / app on localhost:18081
make hooks     # install pre-commit (lint + test) and pre-push (secrets) hooks
make mcp-token-create NAME= SCOPE= / mcp-tokens / mcp-token-revoke ID=   # local MCP tokens
make web-install   # npm ci (the only step that downloads)
make web-dev / web-build / web-lint / web-test / web-types / web-fmt / web-e2e
```

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
- Migrations already on `master` are immutable. A shipped migration makes a rollback
  manual (`alembic downgrade` on the VPS) - the image tag alone does not restore the schema.
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
- **Deploying is the human's decision.** Never run `make deploy`/`make verify`/`/ship` on
  your own, and never ask or suggest whether to deploy; the human deploys manually or asks
  you to, then use `/ship` and do not re-run tests first. What is deployed: `make
  deployed`; never write `.deployed` or record a deployment in the docs by hand.
  Nothing beyond the hook is required: targeted tests while developing, `make check`,
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
- Say "it works on the server" only after a deploy (`make deploy` reports health and smoke).
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

## Current state (2026-10-23)

- **Deployed:** see `make deployed`. **Built:** prepaid foundations P1–P2c4b
  (`0034`–`0042`) retain source-pinned funding, command retries, funded intervals and
  atomic posting. P3a isolates daily prepaid billing. P3b adds effective entitlement
  and independent manual suspension (`0043`). P3c accepts immediate prepaid add
  orders with tax-inclusive holds and number claims, and completes their full
  product/order tree atomically in the worker. P3d adds future-start intents and
  pre-grant cancellation. P3e produces renewals at paid-through and resumes after a
  credit (checkpoint 1). P3f adds manual suspend/resume orders and the prepaid close
  guard. P3g credits unused dates on termination. P3h applies offering changes at a
  funded renewal (`0044`). P3i exposes internal command list/detail/retry with MCP tools.
  P4a adds the `balance_action` schema and adjustment/transfer/reversal sources (`0045`).
  P4b adds the Idempotency-Key response journal on the order POST (`0046`).
  P4c serves TMF654 bucket/accumulatedBalance reads and the TMF654 hub. P4d adds
  `topupBalance` and the top-up every direct payment on a prepaid account records. P4e adds
  `adjustBalance` (credit, debit limited to spendable funds, exact-source cancellation). P4f adds `reserveBalance` operator holds that lapse before
  any later spend. P4g accepts `transferBalance` (hold + queued command). P4h1 completes fee-free transfers in the worker. P4h2 posts transfer fees as one-time charges (`0047`: a fee ABR has no product).
  Public prepaid creation stays gated.
- **Number release evidence:** 1,495 backend tests, 284 frontend tests in each
  timezone, browser checks, lint and docs passed. All number scope items tested.
  [Release evidence](docs/subscriber-number-release.md).
- **Next:** prepaid slice P4i, then down the tracker [prepaid-slices.md](docs/prepaid-slices.md)
  (prepaid creation stays a 422 until P5a) - [scope](docs/prepaid-plan.md),
  [decision](docs/decisions/prepaid.md).
- **Backlog:** sidebar regrouping + global Orders screen, decided 2026-10-21
  ([frontend.md](docs/decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21));
  UI vs MCP gap slices 2 and 4 ([plan](docs/ui-mcp-gap-plan.md));
  progress drift check + per-item deployed
  status; APP_PORT / `make down` profiles (two stacks with vps-lab); vps-lab guard
  backport; changing `billCycleDay` (open question); subscriber-card visual review.
- **Open checks:** network isolation of app/MCP; demo data in prod (pending); DB
  pool confirmed on the VPS; restore drill from a cron-made dump; pytest
  container shows `(unhealthy)`. Detail for all of these: [docs/status.md](docs/status.md).

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
