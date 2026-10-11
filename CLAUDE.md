# bss

FastAPI + PostgreSQL BSS on the TMF Open APIs, with a React admin UI. Developed locally
in WSL2, verified on a remote VPS. Generated from `vps-lab-template`: if an `init:` target
is still in the Makefile, run `make init` first ([infra.md](docs/decisions/infra.md#template-origin-make-init)).

This file holds what every session needs. **Decisions and design detail live in
[`docs/decisions/`](docs/decisions/README.md)** (index at the end) and bind just as
much: read the file for an area before changing it, and record new decisions there.

## Environments

| Where | What runs there |
|-------|-----------------|
| WSL2 (Ubuntu) | full dev loop: build, run, tests, lint |
| VPS `vps-test` (Debian 12, 1 vCPU, 1931 MB RAM, 30 GB disk) | verification only — pre-built image, no build |
| Cloud session (claude.ai/code) | branches only, no Docker build, no VPS: [docs/cloud.md](docs/cloud.md) |

**Images are built locally and shipped to the VPS. Never run `docker build` on the VPS** —
it has one core and a production VPN server running next to us.

## Commands — use these, do not invent your own

```
make up        # start local stack (app + postgres) with hot reload
make down      # stop local stack
make logs      # last 5 min of app logs
make test      # pytest inside the container
make lint      # ruff check + format check
make smoke     # smoke-test the local instance
make deploy    # build locally, ship image to VPS, restart there
make check     # every local check of verify: lint, test, docs-check, decisions-check,
               # web-lint, web-test, web-e2e
make verify    # make check, then deploy (+ health, smoke, UI, web smoke)
make ssh       # open a shell on the VPS
make migrate   # apply Alembic migrations against the local stack
make worker-once  # one wake-up of the billing worker against the local stack, then exit
make psql      # interactive psql in the local db via compose exec (needs a TTY; the db port
               # is not published - non-interactive: docker compose exec -T db sh -c 'psql ...')
make fmt       # auto-format (ruff format + check --fix)
make clean     # stop the local stack AND drop the local database volume
make tunnel    # forward the remote app to localhost:18081
make remote CMD="..."  # run a one-off command against the deployed image on the VPS
make hooks     # install the local pre-commit (lint + test) and pre-push (secrets) hooks
make spec-verify-upstream  # re-fetch spec blob SHAs from GitHub, compare with spec/README.md (network)
make progress  # regenerate docs/PROGRESS.md + docs/progress/status.json (scope.yaml vs the code)
make decisions-check  # nothing of the old CLAUDE.md lost (docs/decisions/README.md)
make seed          # LOCAL demo data through the public TMF API (refuses APP_ENV=prod); re-run = no-op
make web-install   # UI: npm ci into the node_modules volume - the ONLY step that downloads
make web-dev       # UI: Vite dev server on http://localhost:5173 (proxies /tmf-api, /internal)
make web-build     # UI: build frontend/dist (offline)
make web-lint      # UI: tsc, eslint, prettier --check (offline)
make web-test      # UI: vitest (offline)
make web-types     # UI: regenerate TypeScript types from spec/ (offline)
make web-fmt       # UI: prettier --write (offline)
make web-e2e       # UI: Playwright smoke on the built UI + seeded local stack (read-only)
```

`make verify` is the single source of truth for "does it actually work on the server".
It exits non-zero and prints remote logs when anything fails.

## Hard constraints

- The VPS also runs a **SoftEther VPN server**, which is its primary purpose.
  Ports **443, 992, 1194, 500, 4500, 5555** are off limits, and so is **8444** (also a
  VPN port, per the human; no other app may use it - not yet in the guard, see "Next
  session"). Do not touch, inspect
  or reconfigure anything VPN-related.
- Never run `docker system prune`, `docker volume rm`, `docker network prune`,
  or stop/remove containers you did not start. Other people's containers may be there.
- On the VPS the app binds to `127.0.0.1:18081` only. Nothing is published to
  0.0.0.0. To reach it from the workstation use an SSH tunnel: `make tunnel`.
- On the VPS this project lives in `~/bss` (`REMOTE_DIR`, filled in by `make init`).
  Every app from this template gets its own directory; never point two projects at the
  same one — each deploy overwrites `compose*.yaml` and `.env` there.
- Remote commands go through `scripts/deploy.sh` only. Do not open ad-hoc `ssh vps-test '...'`
  sessions that mutate state.
- Never read, print or commit `.env` / `.env.prod` / `.env.smoke`. Use `.env.example` as
  the reference.
- Never export `.env.prod` variables into a shell that also runs the local stack (e.g.
  `set -a && . ./.env.prod`) — exported variables take precedence over `.env`, so local
  containers silently pick up production values. For a one-off check against prod
  variables, scope them to a single command instead: `env $(grep -v '^#' .env.prod | xargs) <command>`.
- **Commits need the human's OK; pushing an approved commit is allowed** (private repo;
  the push is the off-site backup): `git push`, `git push origin <branch>`, `git push -u
  origin <branch>`. **Force, delete, mirror, prune and tag pushes are forbidden**:
  `--force`, `-f`, `--force-with-lease`, `--force-if-includes`, `--mirror`, `--delete`,
  `-d`, `--prune`, `--tags`, `--follow-tags`, and refspecs starting with `+` or `:`. The
  guard blocks them (compound commands, `git -C`, `sh -c` included); history on the
  remote is never rewritten. **If CI ever deploys on push, pushing becomes a deploy and
  goes back to needing approval** - change this rule with it.
- **Cloud sessions push only to `cloud/*` branches, never to `master`** (`git push -u
  origin cloud/<topic>`); the human reviews and merges. Only a local session pushes
  `master`, and only commits the human approved.
- **Do not add `db` or `caddy` services back to the production configuration**
  (`compose.prod.yaml`), and do not re-add a Caddyfile or backup scripts to this repo.
  On the VPS they live in the separate vps-infra project — see ["Shared infrastructure"](docs/decisions/infra.md#shared-infrastructure-vps-infra).
- `make deploy` is no longer fully reversible once a migration has shipped. Rolling the
  image back to a previous tag restores the old code, but not the database schema — if a
  migration already ran, reverting the image leaves the schema ahead of the code. Rolling
  back the schema requires running `alembic downgrade <revision>` by hand against the VPS
  database; there is no automated rollback path.
- The VPS reboots nightly at 03:00 (root cron). Everything must come back on its own —
  never rely on manually started containers.

## Agent guardrails

Full text: [harness.md](docs/decisions/harness.md). `.claude/settings.json` and
`.claude/hooks/guard.sh` (+ `guard.py`, quote-aware) protect against **agent mistakes,
not a hostile agent**; they are a pattern matcher, not a security boundary. The rules
above bind because they are the rules.

- **Secrets:** besides `.env*` themselves, do not run `docker compose config` (prints
  every substituted `${VAR}`) or `docker compose run/exec ... env`; `docker compose
  config --quiet` validates without printing. Read, Grep, `cat` - the route does not
  matter.
- **Harness files need the human's OK**: `.claude/**`, `compose*.yaml`, `Makefile`,
  `scripts/**` (and `docs/progress/scope.yaml`) - changed with the Edit tool only; the
  guard blocks shell writes to them.
- **Pre-commit hook** (`make hooks`; `scripts/pre-commit.sh`): `make lint` + `make test`,
  plus `web-lint`/`web-test` when `frontend/` or `spec/` is staged. It refuses a commit
  unless the working tree is exactly what is committed. `git commit --no-verify` only to
  skip it deliberately (a WIP commit, or a cloud session, where it cannot run - say so in
  the message). Full log in `.git/pre-commit-logs/`.
- **Pre-push hook** (`scripts/pre-push.sh`): refuses bcrypt hashes, `.env*` paths, branch
  deletions and non-fast-forwards in the pushed commits. Never bypass it (`--no-verify`).
- **Cloud mode** (`CLAUDE_CODE_REMOTE=true`, set by Claude Code in every cloud session):
  the SessionStart hook runs `scripts/cloud-setup.sh` (PostgreSQL, venv, `npm ci`); the
  guard allows `npm`/`npx`/`node` on the host, blocks any command naming `vps-test` or
  the VPS's public domain (read those files with the Read tool), and lets a push through
  only as `git push -u origin cloud/<topic>`. The commands that replace the make targets
  there: [docs/cloud.md](docs/cloud.md).

## Working style

- For anything that changes the Dockerfile, compose files, deploy script or the data
  model: propose a plan first, then implement.
- After changing runtime behaviour, run `make test`; before saying "it works on the
  server", run `make verify`.
- When a container misbehaves, read `make logs` before changing code.
- **Files are edited only with the Edit/Write tools** - never through shell heredocs,
  `sed -i`, `python -c`/scripts that rewrite files, or redirects (`>`, `>>`, `tee`).
  Temporary negative-proof mutations included: make them with Edit and revert with Edit.
- **If the guard (or any permission rule) blocks a shell command, stop and report it.**
  Do not reach the same result another way (another tool, a script file, a different
  path spelling) - that is exactly what the block exists to prevent, even when the block
  looks like a false positive. Say what was blocked and why it seemed needed; the human
  decides.
- **Never filter or truncate the output of `git commit`, `make test`, `make verify` or
  the pre-commit hook** - no `grep`, `tail`, `head` (or `| ... | tail -1`) on them. If the
  output is too long, redirect all of it to a file outside the repo (the scratchpad,
  `/tmp`) and read that file. The one unexplained hook failure (see ["Pre-commit hook"](docs/decisions/harness.md#pre-commit-hook))
  is undiagnosable because its commit was run through `grep ... | tail -1`.
- **Commit messages go through a file** (Write it to the scratchpad, then `git commit -F
  <file>`), not a heredoc (`git commit -F - <<'EOF'`). `guard.py` cannot tokenise a
  heredoc, and the raw-text fallback is deliberately broad: a message that mentions VPN
  ports next to ssh/docker is blocked (a regression case locks this in).
- **Per change:** plan, tests, negative proofs (break the rule once, see a test fail,
  revert), `make lint`, `make test` (x3 for a slice), `make progress` when scope items
  change (`scope.yaml` needs the human's approval), the decision recorded in
  `docs/decisions/` (or here, if every session needs it), then show the diff and commit
  only after the human's OK. Small, focused commits; `make lint && make test` before each.

**Rules that bite in every area** (the full rule is in the linked file):
Money is `Decimal`, never float ([data-model.md](docs/decisions/data-model.md)).
Nothing in `app/` reads the wall clock except `app/clock.py` (same file). Ranges are
half-open and billing follows dates, not status (same file). Migrations already on
`master` are immutable; dependencies are pinned, never floating
([infra.md](docs/decisions/infra.md#conventions)). `/healthz` stays cheap and checks the
DB (same file). Unknown attributes are a 400; the contract is the vendored specs, never
`/openapi.json` ([tmf-api.md](docs/decisions/tmf-api.md)). Orders alone create or end
products; only the billing run creates ABRs and bills ([tmf-api.md](docs/decisions/tmf-api.md#tmf637-and-tmf678-mapping)).
Node runs only in containers on the workstation; the UI's write requests are pinned on
both sides ([frontend.md](docs/decisions/frontend.md#frontend-conventions)).

## Next session

**MCP connector** (task of 2026-10-11, [docs/mcp.md](docs/mcp.md)): five slices, each
committed after the human's OK and then pushed; nothing deployed without a separate
approval. Done, local only: slice 1 (server, static tokens, scopes, limits, audit;
`6ae9f40`), slice 2 (OAuth 2.1 + DCR + discovery + login page). Next: 3 (tools from
`app.openapi()` + drift check), 4 (`compose.prod.yaml` + vps-infra change request), 5
(docs + token tooling).

**Before the next deploy** (2026-10-10: bill lifecycle slices 1-4 merged as `1d7a242`,
subscriber create/edit as `61d9399`; local `make check` passed on a clean database,
nothing deployed):

- **`compose.prod.yaml`: pass `BILL_PAYMENT_TERM_DAYS`** to the services that need it
  (the worker writes `paymentDueDate`; `/internal/v1/context` reports it). Compose
  change: diff first, needs the human's OK.
- **Visual review of the subscriber card** with the human before `make verify`: the
  seven figures in one row, the bill dialog, Record payment, New subscriber, Edit.
- **Migration `0007`** (`cloud/quality-1`, if merged): adds
  `applied_customer_billing_rate.applied_at` (an ABR's TMF678 `date` from the clock),
  back-filled from each ABR's run `started_at`, then NOT NULL. Ships with the next
  deploy; rolling back needs `alembic downgrade 0006` by hand (drops the column).

Then, in this order (as of 2026-10-05, after slice f):

1. **Stage 4: slice g merged** (`cloud/billing-runs-ui` as `209955c`, 2026-10-10, after
   a local `make check` incl. web-e2e): the Billing runs screen, UI "tested" in the
   progress tracker, and the fix for the flaky App.test. Not deployed yet: `make verify`
   ships it, together with the bill lifecycle (see "Before the next deploy"). Slice f (`dcbedeb`) is
   **deployed**: `make verify` 2026-10-05 end to end - migration `0004` ran on the VPS,
   app and worker healthy, the worker's first run billed 2026-10-05 (`/billing-status`
   200 `ok`, age 12 s), web smoke OK through Caddy. Done: slice a (`a66faef`), b1 (`850e192`), b2
   (`5e9dbe3`), b3 (`3464e54`), c (`7ad4923`), h (`840e092`), d (`1dbe739`), e
   (`e06a22b`, `/billing-status`), f (`dcbedeb`). Per slice: plan, tests, negative proofs, `make lint`,
   `make test` x3, `make progress`, CLAUDE.md, then show the diff and commit only after
   the human's OK. Worker 10/10, API 54/54 in `docs/PROGRESS.md`. The planning
   decisions Q1-Q14 are recorded (see ["Billing worker"](docs/decisions/worker.md)).
2. **Progress tracker steps 3-5** (after slice b): "tested" (done for db and api on
   `cloud/quality-1`: requests to the app recorded per test - at the `TestClient`, so
   `test_health.py`'s raw clients count too - matched to OpenAPI templates, and
   `pg_stat_user_tables` of each test database before it is dropped; UI: done for
   vitest - `expectUi(id)`, see ["Progress tracker"](docs/decisions/harness.md#progress-tracker);
   Playwright could record the same way),
   the drift check in the pre-commit hook and `make verify` (with negative proofs), and
   "deployed" from `docs/progress/deployed.txt`. Slice b also defines the `worker` area
   (capabilities proven by `@pytest.mark.capability(...)` tests).
3. **Docs task** (tbls + API registry + architecture.md) - planned, not started.
4. **deploy.sh records the deployed commit** (Open check below).
5. **Backport vps-lab's guard to bss** (ports 8444 and 2053, ask on commit/deploy) -
   parked; the push policy is done (2026-10-09). Also: protect
   `docs/progress/scope.yaml` from shell writes (`>`, `tee`, `sed -i`, `mv`, `cp`, `rm`)
   like the other harness files - today only the Edit tool's `"ask"` rule guards it -
   with regression cases both ways.
6. **Remaining Phase 2** (APP_PORT, localhost:8080 refs, make down profiles) - deferred.
7. **Cloud mode, first real run**: guard cloud mode, portable cases and the SessionStart
   setup are done (2026-10-09, "Agent guardrails"; the setup rehearsed in an Ubuntu 24.04
   container). Still unproven in an actual cloud session: the hook firing on startup and
   resume, `CLAUDE_CODE_REMOTE` reaching the hooks, and the setup's `npm ci` /
   `npm run lint` / `npm test` behind the egress proxy (`docs/cloud.md`).
8. **FastAPI's `/docs`, `/redoc`, `/openapi.json` in prod**: decide whether they stay
   exposed (behind basic auth) or get disabled; add the decision to `scope.yaml` either
   way (needs the human's approval).

Also pending, not ordered: the two fixes to port to vps-lab-template
([infra.md](docs/decisions/infra.md#to-port-to-vps-lab-template)), and the open design
questions - changing `billCycleDay`, suspension semantics
([billing-core.md](docs/decisions/billing-core.md#open-questions)).

## Open checks

Nothing below is done; remove an item only when it has passed (or, for work items,
shipped).

- **Stage 4 (the worker)** is in progress - see "Next session" above.
- **bss app is reachable without auth from any container on `shared` and from VPS
  loopback 127.0.0.1:18081** - close with a private app<->mcp network and a Caddy-side
  alias (separate task). Documented as a known demo risk in [docs/mcp.md](docs/mcp.md).
- **Demo data in prod: decision pending.** `make seed` refuses prod on purpose. If prod
  gets demo data, it can only be current state: history cannot be backdated through the
  API (prices and orders in the past are refused), and the seed builds its history with a
  FixedClock in-process, which is not an option against prod.
- **Two stacks side by side with vps-lab** (both on this workstation). **Done: the local
  db no longer publishes 5432** (2026-10-03; tests and the app reach `db` on `lab`, `make
  psql` goes through compose exec). Proven: after `make up` nothing listens on host 5432;
  with another process holding `127.0.0.1:5432`, `make test` created and started `db`
  and passed (before the change it failed with "Bind for 127.0.0.1:5432 failed: port is
  already allocated" while vps-lab's db was up). Still open: `APP_PORT=8081` for bss (the
  publish in `compose.override.yaml`, and the Makefile's `smoke` and health wait, which
  say `localhost:8080` literally); `make down` must also stop the profiled services
  (`web`, `web-offline`, `tools`, ...), which a plain `docker compose down` leaves running.
  Until then, do not run bss's app and vps-lab's app at the same time (both use 8080).
- **Phase 2, rest of the two-stack fix (APP_PORT=8081, localhost:8080 refs, make down
  profiles) - specified, deferred.** The spec (2026-10-03): app host port from
  `APP_PORT`, default 8081; every hardcoded `localhost:8080` in the repo (Makefile,
  scripts/, frontend/, playwright config, docs) driven by it; `make down` also stops
  profiled services. Negative proofs: the stack starts and `make smoke` passes with
  something on host port 8080; a busy `APP_PORT` makes the health wait fail non-zero; no
  bss container after `make down`. Then `make verify` up to (not including) deploy.
  Touches Makefile, compose files, maybe scripts/: diff first.
- **App opens a new DB connection per request (`connect_timeout` was 3 s, now 10 s)** -
  open: how to bound the API's connections on prod (1 vCPU, shared with VPN/Xray).
  **Decided (stage 4 planning):** the API keeps a connection per request with
  `connect_timeout` raised to 10 s (done: `app/config.py`); a pool only after measuring
  on the VPS. The worker holds one dedicated long-lived connection. **Not bounded:** a
  burst could make bss alone ask for more than the 37 connections available - a pool, or
  a cap on concurrent DB connections from the API. Decision deferred. Reproduction,
  budget and options in full: [infra.md](docs/decisions/infra.md#db-connections-per-request-open-check).
- **Record the deployed commit on the VPS.** Finding which image runs took an ad-hoc
  read-only `ssh vps-test 'docker ps ...'` (2026-10-03: `bss-app:9424b9b`, in the current
  history; CLAUDE.md's `921603f` predates a squash). `deploy.sh` should write the deployed
  commit next to the compose files (e.g. `~/bss/DEPLOYED`), and the smoke test should
  print it. Touches `scripts/`: needs the human's OK.
- **The pytest container shows `(unhealthy)` during `make test`**: the image's
  HEALTHCHECK probes `/healthz`, which nothing serves while pytest runs in the `app`
  service. Harmless for `run --rm`, but noise in `docker ps`. Since the 60 s interval
  `unhealthy` needs 3 failed probes after the 30 s start period - longer than a `make
  test` run (~70-90 s) - so it should show `health: starting` instead (not observed). Fix later in compose
  (e.g. disable the healthcheck for one-off runs); compose change, needs the human's OK.
- **Restore drill** from a cron-made dump. On 2026-09-30 `~/infra/backups` on the VPS was empty and no backup
  cron was installed; this is being fixed in vps-infra. Use the first cron-made `bss` dump:
  `pg_restore --list` on the server (no databases created and no `pg_restore` into a
  database there), copy it here, restore into a local `postgres:16-alpine` as a
  NOSUPERUSER role `bss` with `pg_restore --no-owner --role=bss`, compare tables, enum
  types and `alembic_version` with prod (after the first stage 4 deploy: 17 tables incl.
  `alembic_version`, 18 enums, `0004` - counted on the local database at head on
  2026-10-05; before it, as deployed with `9424b9b` on 2026-10-02: 15 tables, 14 enums,
  `0003`), report every warning, clean up locally. Expect the shape of the last deploy
  before the dump.
- **`"ask"` rules.** Verify in a fresh session, in the normal mode, that an Edit to
  `Makefile` prompts; if it doesn't, switch these paths from `"ask"` to `"deny"`.
  **Seen not to prompt in a cloud session** (reported by the human, 2026-10-10): Edits
  under `scripts/**` went through without a prompt there. Open: whether that is the
  cloud session's permission mode or the rule itself, and what replaces it in the cloud
  (a `"deny"` cannot depend on cloud mode; the guard sees only Bash, not the Edit tool).

## Where the decisions are

[`docs/decisions/`](docs/decisions/README.md) keeps the old section names as headings:
[data-model](docs/decisions/data-model.md) (domain model) ·
[tmf-api](docs/decisions/tmf-api.md) (conventions; TMF632/629/666/676/637/678/622) ·
[catalog](docs/decisions/catalog.md) (TMF620, D-W18, D-W21) ·
[billing-core](docs/decisions/billing-core.md) (stage 3, open questions) ·
[worker](docs/decisions/worker.md) (stage 4) · [bills](docs/decisions/bills.md) (bill
lifecycle) · [internal-api](docs/decisions/internal-api.md) · [mcp](docs/decisions/mcp.md) (MCP
connector) ·
[frontend](docs/decisions/frontend.md) · [infra](docs/decisions/infra.md) (image,
layout, conventions, vps-infra, deploying the UI) · [harness](docs/decisions/harness.md)
(hooks, progress tracker, guardrails) · [retired](docs/decisions/retired.md).
