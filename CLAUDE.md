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
make mcp-smoke # MCP connector: discovery, 401, OAuth endpoints (local, http://localhost:8090)
make mcp-token-create NAME=ci SCOPE=read  # local MCP static token, printed once
make mcp-tokens                           # local MCP static tokens + live OAuth grants
make mcp-token-revoke ID=<id>             # or GRANT=<grant id>; on the VPS: docs/mcp.md
make remote SERVICE=mcp CMD="..."         # `make remote` in the mcp service (its volume)
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
  `scripts/**` - changed with the Edit tool only; the
  guard blocks shell writes to them.
- **MCP tools bypass the Bash guard - server-side scopes are the boundary; never add an
  unauthenticated mode.** The connector ([docs/mcp.md](docs/mcp.md)) acts on the deployed
  bss through tool calls that `guard.sh` and the permission rules never see; only the
  token's scope (`read`/`write`, enforced in `bss_mcp`) limits them. A production MCP
  token is a credential: the human creates it, not a session.
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
  change (`scope.yaml` is editable within authorized feature work; preserve existing
  scope unless the task changes it), the decision recorded in
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

**Deployed** (`make verify`, 2026-10-19): **`bss-app:7272ffc`** runs on the VPS -
everything up to and including UI user management: migrations up to `0029`, app
authentication with sessions and roles, the UI's sign-in and Users screen, the
service/smoke secrets, the `terminationInPeriod` fix. Web smoke (incl. "Caddy's login
alone -> 401 from the app") and MCP smoke passed; a real MCP tool call answered 200 as
`mcp:demo`. Rolling back past `0029`/`0028` needs `alembic downgrade` by hand (see
below). Until U11 the UI asks for two logins (Caddy, then the app).

**Following TMF** (2026-10-17, the human's rule: what the vendored specs define is
implemented, the rest skipped; "proceed with development"). Slice 0 done locally: the
exact gap list `tests/tmf-gaps.txt` (831 lines), probed by `tests/tmf_gaps.py` and
locked by `tests/test_tmf_gaps.py` - each slice removes the lines it closes (`94b6ff2`).
Slice 1 (nothing dropped or rewritten; migration `0021`; 705 lines left) done locally,
[decision](docs/decisions/tmf-api.md#nothing-dropped-or-rewritten-2026-10-17); pushed
`4bd8baa`. Slice 2 (PATCH in all four spec formats, JSON Patch and JSON Patch Query;
651 lines left), [decision](docs/decisions/tmf-api.md#patch-formats-2026-10-17); pushed
`a8b42e3`. Slice 3 (2026-10-18; base paths: TMF629 `/tmf-api/customer/v5`, TMF676
`/tmf-api/payment/v4`, straight rename, no aliases; 649 lines left),
[decision](docs/decisions/tmf-api.md#base-paths-2026-10-18); committed and pushed.
Both base paths moved with the deploy of `d499338` (the UI and MCP with them).
Slice 4 (2026-10-18; TMF632/629 attributes, migration `0022`; 476 lines left, none for
TMF632/629): [decision](docs/decisions/tmf-api.md#party-and-customer-attributes-2026-10-18);
committed and pushed `6743dc6`. Descriptive attributes are jsonb documents checked
against `spec/` at runtime; **the runtime image now ships `spec/`** (Dockerfile).
Slice 5 (2026-10-18; TMF666 attributes, migration `0023`; 375 lines left, none for
TMF666): [decision](docs/decisions/accounts.md#account-attributes-2026-10-18) - `relatedParty`
in any role around one resolved anchor (customer/owner, changeable while nothing depends
on it), documents in `details`, `paymentStatus` a record, `ratingType` always
`postpaid`, `billCycleDay`/`currency` optional (`UI_CURRENCY` is now also the default
currency). Committed and pushed `9941a9f`. Slice 6 (2026-10-18; TMF620 attributes,
migration `0024`; 161 lines left, none for TMF620):
[decision](docs/decisions/catalog.md#catalog-attributes-2026-10-18) - documents
(own-catalog references in them stored as sent), inline prices, a price's charge and an
offering's specification changeable until the offering is ordered, the code one
`externalIdentifier` entry, reference versions checked; committed and pushed `f476baa`.
**Pending from slice 6, needs the human's OK** (harness file):
`scripts/docs/api-notes/tmf620-product-catalog.md` still says category membership is
read-only on the offering, prices are by reference only, a price's charge is read-only,
the code is set on POST only and the specification is mandatory, and names two renamed
tests (now `test_the_code_changes_with_patch_and_stays_unique`,
`test_an_offering_specification_is_optional_but_must_exist`); propose the diff, then
`make docs`.
Slice 7 (2026-10-18; taxes, migration `0025`; 156 gaps left):
[decision](docs/decisions/billing-core.md#taxes-2026-10-18) - the human: "decide based
on TMF recommendations". A price's `tax[]` is applied per ABR (rate in percent, the
specs' majority; half-even per line; refunds by diff at the charge's rate), part of the
price's charge; a billing account's `taxExemption` exempts by `taxType` (full only);
bills, balance, settlement and the internal views are tax-included; TMF676 `taxAmount`
splits `totalAmount`. UI and seed unchanged. API notes for TMF676/678 updated with the
human's OK. Committed and pushed `381795d`.
Slice 8 (TMF622; the human: "decide based on TMF" for all four open choices) is split.
Slice 8a (2026-10-18; order/item documents, optional derived `billingAccount`,
`noChange` executed, nested items, drafts with PATCH submit, migration `0026`; 100 gaps
left, none for TMF622): [decision](docs/decisions/tmf-api.md#order-attributes-and-drafts-2026-10-18);
API notes for TMF622 updated with the human's OK; committed and pushed `e5db5a5`.
Slice 8b (2026-10-18; `modify` items, migration `0027`; 100 gaps left, none new):
design approved, then implemented locally -
[ordering](docs/decisions/tmf-api.md#modify-items-2026-10-18),
[billing](docs/decisions/billing-core.md#offering-changes-2026-10-18). A modify item
changes a product's offering from `requestedStartDate`, billed prorated per segment;
offering history in `product_offering_change`; ABRs carry their offering;
**`abr_no_overlap` replaced by `abr_one_per_start`** (the human's choice: a charge is
refunded, never shortened, so charges overlap after a mid-period change); API notes for
TMF622 and `scripts/docs/generate.py` updated with the human's OK. Committed and
pushed `eaa1a7b`.
**Next session: UI user management** (the human, 2026-10-19): a user menu in the UI,
roles `admin` (manages users, does everything) and `user` (everything but user
management), everyone changes their own password. The plan:
[users.md](docs/decisions/users.md#ui-user-management-the-plan-2026-10-19) - the app
takes over authentication (users, sessions, every route protected, a service credential
for MCP), three slices (backend, UI, deploy). **Q1-Q7 approved with all proposed defaults.**
Backend slice committed and pushed as `1c9f216` (migration `0029`): three full test
passes (1,345 each), lint, frontend unit checks, docs and progress generation passed;
commit hooks also passed. The human approved and applied the docs-generator mappings
and API note. **`1c9f216` is live on the VPS** (seen 2026-10-19: the production MCP
connector gets `401 sign in required`), so migrations `0028`/`0029` ran there - **and
production MCP and the web smoke are broken until slice 3**: `compose.prod.yaml` passes
no `BSS_SERVICE_SECRET`/`BSS_SMOKE_SECRET`, `web-smoke.sh` still expects Caddy's
`X-Remote-User`. The UI needs a first admin on the VPS.
UI slice 2 committed and pushed (2026-10-19, the human's OK): session gate, `/sign-in`,
forced password change, operator menu, admin `/users`, pinned request/component tests
and browser sign-in. 250 frontend tests passed in UTC and New York; `make test` 1,346
passed. CSRF and UI role-check negative proofs failed as expected and were reverted.
Slice 3 wiring committed and pushed (2026-10-19, the human's OK):
[users.md](docs/decisions/users.md#deployment-wiring-slice-3-2026-10-19) -
`BSS_SERVICE_SECRET`/`BSS_SMOKE_SECRET` from `.env.prod` in `compose.prod.yaml`, the
smoke secret also in `.env.smoke` for `web-smoke.sh`, `make web-e2e` with disposable
fixture users. The first admin exists on the VPS (the human, 2026-10-19). `make verify`
(2026-10-19) stopped at `make test`: the `ci` profile found a billing gap live since
`d499338` (a modify after a same-period termination refund = a second refund, the
account's billing stops); fixed with the human's choice, the `terminationInPeriod`
ordering rule ([billing-core.md](docs/decisions/billing-core.md#offering-changes-2026-10-18)).
API note added with the human's OK (`7272ffc`). Deployed with `7272ffc` (see
"Deployed" above). **Next: the vps-infra change request removing Caddy's basic auth from
the bss site (U11)**, now that app sign-in works on production.
Bootstrap CLI: `python -m app.users create NAME --role admin`.

**The last gaps** (2026-10-19; plan confirmed by the human, billing unchanged by
suspensions; migration `0028`): committed and pushed -
[decision](docs/decisions/tmf-api.md#the-last-gaps-2026-10-18),
[suspension](docs/decisions/tmf-api.md#suspension-2026-10-19),
[alteration prices](docs/decisions/catalog.md#alteration-price-types-2026-10-19). The
probe goes one level into responses; `tests/tmf-gaps.txt` has **no open gap** and six
accepted deviations with reasons. API notes for TMF622/637/676/678 and the docs `AREAS`
entry applied with the human's OK. **The TMF gap work is done.** Migration `0028` is deployed
(with `1c9f216`, then `7272ffc`); rolling back needs `alembic downgrade 0027` by hand
(after `0029`'s downgrade to `0028`); it refuses while a status item or a
discount/allowance/penalty price exists.

**100% TMF coverage** (2026-10-17, approved): TMF637 product POST/PATCH/DELETE as orders
placed on the caller's behalf (a would-be rejection is a 409, nothing stored; DELETE ends
today and the product stays readable), billingCycleSpecification writes (named monthly
specifications only, migration `0019`, accounts may name one), PATCH productOrder
(description, category, priority, externalId; migration `0020`). Coverage 129/129.
Decision: [tmf-api.md](docs/decisions/tmf-api.md#product-writes-through-orders-2026-10-17).
Deployed with `d499338`.

**Event notifications** (2026-10-17, approved plan: three slices, all 17 `/hub`
operations, real delivery). Slice 1: hubs in every API, callbacks https-only to public
addresses outside `APP_ENV` dev/test (the VPS runs `staging`), migration `0017`. Slice 2:
transactional outbox (`0018`), events from every TMF write, orders, bills and the worker,
each payload validated against its listener schema. Slice 3: the worker's step 6 POSTs
due deliveries (retries 1 min ... 12 h, then failed), re-checks and pins the address
before each send, purges after 30 days. Coverage 122/129 (94.6%). Decision:
[events.md](docs/decisions/events.md). Deployed with `d499338`: the worker makes outbound
HTTPS calls to hub callbacks (none until a hub exists).

**Another 10%: deletes and order cancellation** (2026-10-17, approved plan: two slices,
13 operations, no `/hub`; cancel failures are a 409 with nothing stored; the UI gets an
"Undo deactivation" for products pending termination). Slice 1 (9 operations, no
migration): DELETE on individual, organization, customer, billingAccount,
financialAccount, partyAccount, settlementAccount, billFormat, billPresentationMedia
for entries nothing references (a billing account: no history at all); writers take
what they reference FOR KEY SHARE, the worker skips a deleted account. Coverage 101/129
(78.3%). Decision: [tmf-api.md](docs/decisions/tmf-api.md#deleting-parties-customers-and-accounts-2026-10-17).
API notes added with the human's OK. Committed and pushed (`656c7fc`). Slice 2 (4
operations): cancelProductOrder undoes a completed order before any of it takes effect
(409, nothing stored, otherwise), DELETE removes rejected orders, migration `0016`
`cancel_product_order` + order cancellation columns, UI "Undo deactivation". Coverage
105/129 (81.4%). Decision: [tmf-api.md](docs/decisions/tmf-api.md#order-cancellation-and-deleting-rejected-orders-2026-10-17).
Deployed with `d499338`.

**TMF620 completion** (2026-10-16, approved plan: two slices, 13 operations, no `/hub`).
Slice 1 (catalog deletes, 5 operations): DELETE on specification, offering, price,
category and catalog for entries nothing references (D1 narrowed to TMF620); orders now
hold their offerings `FOR SHARE`. Coverage 84/129 (65.1%). Decision:
[catalog.md](docs/decisions/catalog.md#deleting-catalog-entries-2026-10-16). Slice 1
committed and pushed (`284ada1`). Slice 2: importJob/exportJob (8 operations),
migration `0015` `catalog_job`; documents inline as `data:` URLs both ways (no download
endpoint - a change from the plan, because the spec's ExportJob_FVO requires `url`);
import under POST's rules, all or nothing. Coverage 92/129 (71.3%). Decision:
[catalog.md](docs/decisions/catalog.md#import-and-export-jobs-2026-10-16). Deployed with
`d499338`.

**TMF666 accounts** (2026-10-16): two approved slices, both committed. Slice 1:
financial accounts (4 operations) owned by a party, billing accounts linked via
`financialAccount`, per-currency balance, read-only UI screen; migration `0013`. Slice 2:
party and settlement accounts (8 operations), records only (no ledger), linkable to the
same owner's financial account; migration `0014`. TMF operation coverage: 79/129
(61.2%); scoped API items: 95. Decision: [accounts.md](docs/decisions/accounts.md).
Deployed with `d499338`. Not done: API notes for the new resources in
`scripts/docs/api-notes/tmf666-account.md` (a harness file).

**Refunds and bill delivery** (2026-10-16): approved milestone adds 14 TMF operations:
refunds, on-demand bills, bill formats and presentation media. Migrations `0010`-`0012`
add six tables, payout credit consumption, off-cycle documents and presentation
snapshots. Decision: [billing-completion.md](docs/decisions/billing-completion.md).
TMF operation coverage: 67/129 (51.9%); scoped API items: 83. On-demand bills wait for
the next successful account run to see the request day's orders. Formats are detailed
or summary JSON; media are preferences, not external delivery. The documentation
script edits were approved and applied by the human. Commit, push and a deployment
attempt were separately authorized on 2026-10-16.

**TMF620 catalog organization** (2026-10-16): local feature slice adds category and
product catalog list/retrieve/create/PATCH (eight operations), hierarchy validation,
root-category catalog membership and category/offering navigation. Migration `0009`
adds four tables, leaving existing offerings unclassified. Decision:
[catalog.md](docs/decisions/catalog.md#categories-and-product-catalogs-2026-10-16).
TMF non-listener operation coverage: 53/129 (41.1%); scoped API items: 69.
Included in the commit, push and deployment-attempt approval of 2026-10-16.

**TMF input/read completeness** (2026-10-14): one local slice implements POST type
checks, strict write queries/fields, explicit errors for unsupported comma filters,
product/order monthly price views, customer/account and product navigation, and
read-only bill cycles with bill links, next dates and tax-free totals. Migration
`0008` snapshots prices for new completed add items; old orders keep unknown quotes.
Decisions: [tmf-api.md](docs/decisions/tmf-api.md#input-correctness-and-read-completeness-2026-10-14).
New tests: `tests/test_api_read_completeness.py`; progress scope is now 59 API items.
Committed; deployed with `d499338`.

**MCP connector** (task of 2026-10-11, [docs/mcp.md](docs/mcp.md)): five slices, each
committed after the human's OK and then pushed; nothing deployed without a separate
approval. Done, local only: slice 1 (server, static tokens, scopes, limits, audit;
`6ae9f40`), slice 2 (OAuth 2.1 + DCR + discovery + login page; `15674e0`), slice 3 (57
tools generated from `app.openapi()` + drift check; `db765f1`), slice 4 (`compose.prod.yaml`
service, deploy.sh checks, `scripts/mcp-smoke.sh`, vps-infra change request; rehearsed
with Caddy 2.11.4, not deployed; `5f2e436`), slice 5 (client setup docs, token tooling
`python -m bss_mcp.tokens` + `make mcp-token*`). All slices done; the production
connector was verified by the human with Dify, Claude, ChatGPT and others (2026-10-19).

**Before the next deploy** - all shipped with `d499338` (2026-10-19; was: 2026-10-10,
bill lifecycle slices 1-4 merged as `1d7a242`, subscriber create/edit as `61d9399`):

- ~~**`compose.prod.yaml`: pass `BILL_PAYMENT_TERM_DAYS`**~~ done (the human,
  2026-10-19): passed to the services that use it.
- **Visual review of the subscriber card** with the human before `make verify`: the
  seven figures in one row, the bill dialog, Record payment, New subscriber, Edit.
- **Migration `0007`** (`cloud/quality-1`, if merged): adds
  `applied_customer_billing_rate.applied_at` (an ABR's TMF678 `date` from the clock),
  back-filled from each ABR's run `started_at`, then NOT NULL. Ships with the next
  deploy; rolling back needs `alembic downgrade 0006` by hand (drops the column).
- **The MCP connector**: done (the human, 2026-10-19) - vps-infra added the Caddy route
  ([docs/vps-infra-change-request.md](docs/vps-infra-change-request.md#2026-10-11-route-the-mcp-connector)),
  `MCP_USERS` is in `.env.prod`, and the production connector was verified with Dify,
  Claude, ChatGPT and others. Every deploy still needs the human's separate approval.

Then, in this order (as of 2026-10-05, after slice f):

1. **Stage 4: slice g merged** (`cloud/billing-runs-ui` as `209955c`, 2026-10-10, after
   a local `make check` incl. web-e2e): the Billing runs screen, UI "tested" in the
   progress tracker, and the fix for the flaky App.test. Deployed with `d499338`,
   together with the bill lifecycle. Slice f (`dcbedeb`) is
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
   parked; the push policy is done (2026-10-09). Progress scope is now editable within authorized feature work; use
   Edit/apply_patch for changes and `make progress` for the generated reports.
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
question of changing `billCycleDay`
([billing-core.md](docs/decisions/billing-core.md#open-questions); suspension semantics
closed 2026-10-19).

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
  **Also not prompted locally** (2026-10-18, auto mode): five Edits to
  `scripts/docs/generate.py` went through without a prompt - the human had approved
  slice 3 beforehand, but the rule did not ask.

## Where the decisions are

[`docs/decisions/`](docs/decisions/README.md) keeps the old section names as headings:
[data-model](docs/decisions/data-model.md) (domain model) ·
[tmf-api](docs/decisions/tmf-api.md) (conventions; TMF632/629/666/676/637/678/622) ·
[catalog](docs/decisions/catalog.md) (TMF620, D-W18, D-W21) ·
[accounts](docs/decisions/accounts.md) (TMF666 financial/party/settlement accounts) ·
[events](docs/decisions/events.md) (TMF hubs, callbacks, event delivery) ·
[billing-core](docs/decisions/billing-core.md) (stage 3, open questions) ·
[worker](docs/decisions/worker.md) (stage 4) · [bills](docs/decisions/bills.md) (bill
lifecycle) · [internal-api](docs/decisions/internal-api.md) · [mcp](docs/decisions/mcp.md) (MCP
connector) · [users](docs/decisions/users.md) (UI users, roles, sessions) ·
[frontend](docs/decisions/frontend.md) · [infra](docs/decisions/infra.md) (image,
layout, conventions, vps-infra, deploying the UI) · [harness](docs/decisions/harness.md)
(hooks, progress tracker, guardrails) · [retired](docs/decisions/retired.md).
