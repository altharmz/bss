# Project status log

The detailed status that used to live in CLAUDE.md ("Next session", "Open checks"),
moved here on 2026-10-20 to keep the per-session context small. CLAUDE.md keeps a
short summary; newest entries first. Links are relative to the repository root as
written in CLAUDE.md, so they are rewritten to `../` here.

## Next session

**P2b2 funding plans (2026-10-21).** The locked legacy adapter now quotes exact
payment/credit-note amounts capped by spendable ledger balance, preserving funds
needed for held bills and unbilled charges. Plans do not write or reserve money.
No runtime caller or migration; prepaid creation remains unavailable. Next:
persisted holds/consumption and combined database bounds. See the
[P2b2 decision](decisions/prepaid.md#p2b2-obligation-aware-funding-plans-2026-10-21).

**P2b1 legacy funding adapter (2026-10-21).** Adds a locked repository snapshot
over existing payment/credit-note sources, combined allocation/payout consumption,
positive bill and unbilled obligations, with independent ledger reconciliation.
No runtime caller or migration yet; prepaid creation stays unavailable. P2a's pure
selector is `29abe5f`. Next: persisted holds/consumption and combined database
bounds, then wire consuming paths and durable worker/recovery. See the
[P2b1 decision](decisions/prepaid.md#p2b1-legacy-funding-repository-2026-10-21).

**P1 prepaid identity foundation (2026-10-21).** Migration `0034` adds stored
account mode (existing/new accounts default postpaid), the prepaid zero-credit-limit
rule, history protection for mode changes and stable account/currency bucket and
accumulatedBalance identities. No balance column or financial/worker behavior is
introduced; prepaid creation remains rejected. The migration preserves populated
finances and immutable refunds and refuses a lossy downgrade. See
[P1 decision](decisions/prepaid.md#p1-account-mode-and-balance-identities-2026-10-21).
Implementation commit: `5992cde`. The commit hook passed all 1,507 backend tests,
lint/format and generated-documentation drift checks. The initial full run's sole
failure was an existing schema-count assertion (44 instead of the new 45); it was
corrected before the successful hook run. All 12 new prepaid persistence tests pass.
Next: shared funding sources, holds and combined bounds across settlement, refunds
and prepaid operations, then durable worker/recovery. Stages 3–4 remain gated together.

**Number milestone deployed (2026-10-21).** `make deploy` shipped `013da24` (N5/N6
and the process updates) through migration `0033`. Health, web and MCP smoke passed.
The remote smoke read `~/bss/DEPLOYED` and matched
`013da24b739a923e295067732c7fd5353f8ef411`; deployment identity rollout is confirmed.
Older pending-deployment notes below are historical.

**N6 subscriber-number release validation (2026-10-21).** Local validation is
complete: lock/quarantine negative proofs failed as expected and were reverted;
three full backend passes each passed 1,495 tests (CI, dev, progress collector);
284 frontend tests in each timezone, five browser smokes, backend/frontend lint
and documentation drift checks passed. The initial backend attempt had one
heartbeat timing failure under concurrent load; subsequent passes succeeded without
code changes. Progress was collected successfully; five TMF639 scope parameter labels
were corrected before rerendering from unchanged evidence. All number scope items
are implemented/tested, no items are unscoped, and TMF654 runtime remains planned.
See [release evidence](subscriber-number-release.md). No deployment was performed
yet; commit, push and deploy follow the normal cycle (no approval). After shipping
numbers, the next development milestone is prepaid schema/funding and worker core
behind unavailable prepaid account creation. Confirm `~/bss/DEPLOYED` on deployment.

**N5 subscriber-number UI (2026-10-21).** Adds `/numbers`, inventory creation/search,
assignment details and locks, requiring-offering number selection, an optional initial
product step in New subscriber, and order-based number changes from the card. Request
bodies are pinned in component/backend tests; browser smoke covers inventory and the
creation form. Validation: 1,495 backend tests; 284 frontend tests in each of UTC and
America/New_York; backend/frontend lint; five browser smoke tests passed. The first
backend fixture attempted to change an immutable price start (corrected to order on
the valid date); frontend lint found non-null assertions (replaced with validated
inventory projections); the navigation assertion was updated for Numbers.
Not deployed: N6 release validation and separate deployment approval follow. The
picker shows currently available unlocked inventory; future releases require refresh.

**N4 deployed (2026-10-20).** The human confirms that N4 (`47b7525`) is now also
deployed to the VPS, through migration `0033`. This session did not independently
inspect the VPS image tag or smoke results. The implementation commit passed all
1,494 backend tests, lint and generated-documentation drift checks; local migrations
also succeeded. Working tree was clean after push. Next: N5 subscriber-number UI,
then N6 release validation. Earlier pending-deployment notes below are historical.

**N4 subscriber-number orders (2026-10-20).** Add/modify/delete/cancel orders now
manage effective-dated number claims, including future reservations and quarantine.
Migration `0033` snapshots the requirement for new products and permits number-only
modify items. TMF637 exposes effective resources and lookup by realizingResource.id;
existing unchecked resource documents remain writable. Next: N5 UI, then N6 release
validation and separate deployment approval. Details: [decision](decisions/prepaid.md#n4-subscriber-numbers-through-orders-2026-10-20).

**N3 subscriber-number inventory (2026-10-20).** TMF639 resource CRUD and hubs,
clock-derived allocation state, transactional events and eight MCP tools are
implemented. Number identity/specification are immutable; only never-assigned
numbers may be deleted. Contract probes now cover TMF639 attributes and formats;
TMF654 remains planned. Next: N4 orders, then N5 UI and N6 release validation.
Deployment still awaits the independent number milestone and separate approval.

**N2 subscriber-number persistence (2026-10-20).** Migration `0032` adds canonical
number inventory and effective-dated, order-linked assignment history, with overlap
exclusions for number/product, cancelled audit versions and a downgrade barrier.
Existing product and financial behavior is unchanged. Next: N3 inventory API;
orders/UI follow in N4/N5. Deployment awaits the independent number milestone and
separate approval. Details: [decision record](decisions/prepaid.md#n2-subscriber-number-persistence-2026-10-20).

**Subscriber numbers and prepaid design finalized (2026-10-20).** The human requested
design completion, choosing remaining policies according to TMF, and authorized its
commit/push. Development starts in a separate session: [scope](prepaid-plan.md),
[implementation hand-off](prepaid-implementation.md),
[decision record](decisions/prepaid.md). N1 is complete locally: both pinned
contracts are vendored unchanged, 24 contract fixtures pass, and all 36 new endpoints
are recorded as planned/absent. Three full backend passes (1,433 each, dev/CI/dev),
frontend checks, provenance and documentation checks pass; progress is regenerated.
Validation detail and the cached scope-label correction are in the decision record.
At N1, no number/prepaid runtime or migration existed. The human authorized
committing/pushing N1; no deployment is authorized or performed for that slice.

**Current release (2026-10-20): sidebar count and product charge periods.**
The financial accounts sidebar now shows its total. New offering supports daily,
weekly, monthly and yearly charges with multipliers 1..1000. The human chose product
charge periods only (invoices remain monthly) and activation-date anniversaries with
a full first interval for the new cadences. Monthly length-1 retains existing calendar
alignment/proration. Decision and implementation detail:
[charge-periods.md](decisions/charge-periods.md). Migration `0030` is applied to
both local and production databases. Downgrade refuses non-monthly or
length-not-1 prices rather than discarding them.

Validation completed: three full backend passes (1,398, then 1,405 twice; the later
passes include seven additional regression cases), 258 frontend tests in both UTC and
America/New_York, four Playwright browser smokes, backend/frontend lint and decisions
check. Negative proofs for the missing sidebar count and a week miscalculated as a day
failed as expected and were reverted. The human approved the three charge-period API
note edits under `scripts/docs/api-notes/`; the edit hook still blocked them, so the
human applied the supplied patch from the shell and it was verified. API/database
reference docs are regenerated with `make docs`; `make docs-check` passed after commit.
Progress scope has not changed.

**Deployed (2026-10-20): `bss-app:7ad101e`**, committed and pushed to `master`.
`make verify` passed all local checks: 1,405 backend tests with the CI profile,
documentation/decision checks, frontend lint, 258 frontend tests in each timezone and
four browser tests. Its deployment applied `0030` and reached healthy app/worker/MCP,
but a public web health request timed out. The first `make deploy` retry passed the
web smoke but timed out on an MCP request; the second retry succeeded end to end,
including all eight web checks and all MCP checks. `/healthz` and `/billing-status`
returned 200 (`ok`). Full logs: `/tmp/bss-charge-period-verify.log`,
`/tmp/bss-charge-period-deploy-retry.log`, `/tmp/bss-charge-period-deploy-retry2.log`.
No runtime changes followed these checks; the release-status follow-up is documentation
only and needs no redeploy. The older slice-6 catalog documentation corrections were
completed locally in the subsequent documentation/backlog cleanup described below.

**Previous deployed release** (2026-10-19): **`bss-app:b4ccd63`** - everything up to
and including UI user management (`7272ffc`: migrations up to `0029`, app
authentication with sessions and roles, the UI's sign-in and Users screen, the
service/smoke secrets, the `terminationInPeriod` fix) and the after-U11 slice 4
(`b4ccd63`, no migration). `make verify`'s checks passed; its deploy hit one transient
connect timeout in the web smoke, the re-run `make deploy` passed the new web smoke
(7/7) and failed only the MCP smoke's stale "everything else -> 401 Basic" check, fixed
in the commit after it (12/12 against production, read-only). Rolling back past
`0029`/`0028` needs `alembic downgrade` by hand (see below). Since U11 (vps-infra
`7926cc2`) Caddy asks for no login; the app's sign-in is the only one. vps-infra has
deleted the old basic-auth hashes and the human the `SMOKE_BASIC_AUTH` line
(2026-10-19): **U11 is closed on both sides.**

**Following TMF** (2026-10-17, the human's rule: what the vendored specs define is
implemented, the rest skipped; "proceed with development"). Slice 0 done locally: the
exact gap list `tests/tmf-gaps.txt` (831 lines), probed by `tests/tmf_gaps.py` and
locked by `tests/test_tmf_gaps.py` - each slice removes the lines it closes (`94b6ff2`).
Slice 1 (nothing dropped or rewritten; migration `0021`; 705 lines left) done locally,
[decision](decisions/tmf-api.md#nothing-dropped-or-rewritten-2026-10-17); pushed
`4bd8baa`. Slice 2 (PATCH in all four spec formats, JSON Patch and JSON Patch Query;
651 lines left), [decision](decisions/tmf-api.md#patch-formats-2026-10-17); pushed
`a8b42e3`. Slice 3 (2026-10-18; base paths: TMF629 `/tmf-api/customer/v5`, TMF676
`/tmf-api/payment/v4`, straight rename, no aliases; 649 lines left),
[decision](decisions/tmf-api.md#base-paths-2026-10-18); committed and pushed.
Both base paths moved with the deploy of `d499338` (the UI and MCP with them).
Slice 4 (2026-10-18; TMF632/629 attributes, migration `0022`; 476 lines left, none for
TMF632/629): [decision](decisions/tmf-api.md#party-and-customer-attributes-2026-10-18);
committed and pushed `6743dc6`. Descriptive attributes are jsonb documents checked
against `spec/` at runtime; **the runtime image now ships `spec/`** (Dockerfile).
Slice 5 (2026-10-18; TMF666 attributes, migration `0023`; 375 lines left, none for
TMF666): [decision](decisions/accounts.md#account-attributes-2026-10-18) - `relatedParty`
in any role around one resolved anchor (customer/owner, changeable while nothing depends
on it), documents in `details`, `paymentStatus` a record, `ratingType` always
`postpaid`, `billCycleDay`/`currency` optional (`UI_CURRENCY` is now also the default
currency). Committed and pushed `9941a9f`. Slice 6 (2026-10-18; TMF620 attributes,
migration `0024`; 161 lines left, none for TMF620):
[decision](decisions/catalog.md#catalog-attributes-2026-10-18) - documents
(own-catalog references in them stored as sent), inline prices, a price's charge and an
offering's specification changeable until the offering is ordered, the code one
`externalIdentifier` entry, reference versions checked; committed and pushed `f476baa`.
**Slice-6 documentation correction completed locally (2026-10-20):** the human
approved and applied the protected source-note patch. TMF620 notes now describe
writable category membership, inline prices, charge changes before ordering,
changeable offering codes and optional specifications; test names are current.
`make docs` regenerated the API reference from these corrected notes.
Slice 7 (2026-10-18; taxes, migration `0025`; 156 gaps left):
[decision](decisions/billing-core.md#taxes-2026-10-18) - the human: "decide based
on TMF recommendations". A price's `tax[]` is applied per ABR (rate in percent, the
specs' majority; half-even per line; refunds by diff at the charge's rate), part of the
price's charge; a billing account's `taxExemption` exempts by `taxType` (full only);
bills, balance, settlement and the internal views are tax-included; TMF676 `taxAmount`
splits `totalAmount`. UI and seed unchanged. API notes for TMF676/678 updated with the
human's OK. Committed and pushed `381795d`.
Slice 8 (TMF622; the human: "decide based on TMF" for all four open choices) is split.
Slice 8a (2026-10-18; order/item documents, optional derived `billingAccount`,
`noChange` executed, nested items, drafts with PATCH submit, migration `0026`; 100 gaps
left, none for TMF622): [decision](decisions/tmf-api.md#order-attributes-and-drafts-2026-10-18);
API notes for TMF622 updated with the human's OK; committed and pushed `e5db5a5`.
Slice 8b (2026-10-18; `modify` items, migration `0027`; 100 gaps left, none new):
design approved, then implemented locally -
[ordering](decisions/tmf-api.md#modify-items-2026-10-18),
[billing](decisions/billing-core.md#offering-changes-2026-10-18). A modify item
changes a product's offering from `requestedStartDate`, billed prorated per segment;
offering history in `product_offering_change`; ABRs carry their offering;
**`abr_no_overlap` replaced by `abr_one_per_start`** (the human's choice: a charge is
refunded, never shortened, so charges overlap after a mid-period change); API notes for
TMF622 and `scripts/docs/generate.py` updated with the human's OK. Committed and
pushed `eaa1a7b`.
**After U11: done** (2026-10-19, `b4ccd63`, deployed;
[as built](decisions/users.md#after-u11-as-built-slice-4-2026-10-19)): operator
from the session, `APP_TRUST_FORWARDED` throttle, web and MCP smokes without basic auth.
**Completed MCP end-to-end review** (2026-10-19) - work list with verdicts and
slices A-C in [docs/mcp-e2e-review.md](mcp-e2e-review.md). All three are implemented
in `40805f4`, included in deployed `7ad101e` (no migration); the human approved same-day status undo and scheduled
termination of future products. Three full test passes (1,366 each), lint and decision
checks pass. The human approved a one-time shell application of the reviewed harness
patch: the local MCP `spec/` mount and TMF622/637 API notes are applied, docs regenerated,
and local MCP smoke passes. Commit, push and deployment completed; the latest
release verification is recorded above. Dated production follow-ups remain open.

**UI user management** (the human, 2026-10-19): a user menu in the UI,
roles `admin` (manages users, does everything) and `user` (everything but user
management), everyone changes their own password. The plan:
[users.md](decisions/users.md#ui-user-management-the-plan-2026-10-19) - the app
takes over authentication (users, sessions, every route protected, a service credential
for MCP), three slices (backend, UI, deploy). **Q1-Q7 approved with all proposed defaults.**
Backend slice committed and pushed as `1c9f216` (migration `0029`): three full test
passes (1,345 each), lint, frontend unit checks, docs and progress generation passed;
commit hooks also passed. The human approved and applied the docs-generator mappings
and API note. **Historical rollout checkpoint:** `1c9f216` was seen live on
2026-10-19, confirming migrations `0028`/`0029` had run. At that point production MCP
and web smoke awaited slice 3's service/smoke secrets and the first admin. These
rollout issues were resolved by the subsequent slices and verified releases above.
UI slice 2 committed and pushed (2026-10-19, the human's OK): session gate, `/sign-in`,
forced password change, operator menu, admin `/users`, pinned request/component tests
and browser sign-in. 250 frontend tests passed in UTC and New York; `make test` 1,346
passed. CSRF and UI role-check negative proofs failed as expected and were reverted.
Slice 3 wiring committed and pushed (2026-10-19, the human's OK):
[users.md](decisions/users.md#deployment-wiring-slice-3-2026-10-19) -
`BSS_SERVICE_SECRET`/`BSS_SMOKE_SECRET` from `.env.prod` in `compose.prod.yaml`, the
smoke secret also in `.env.smoke` for `web-smoke.sh`, `make web-e2e` with disposable
fixture users. The first admin exists on the VPS (the human, 2026-10-19). `make verify`
(2026-10-19) stopped at `make test`: the `ci` profile found a billing gap live since
`d499338` (a modify after a same-period termination refund = a second refund, the
account's billing stops); fixed with the human's choice, the `terminationInPeriod`
ordering rule ([billing-core.md](decisions/billing-core.md#offering-changes-2026-10-18)).
API note added with the human's OK (`7272ffc`). Deployed with `7272ffc` (see
"Deployed" above). U11 (Caddy's basic auth removed) is live on the vps-infra side; the
bss follow-up is complete ("After U11: done" above).
Bootstrap CLI: `python -m app.users create NAME --role admin`.

**The last gaps** (2026-10-19; plan confirmed by the human, billing unchanged by
suspensions; migration `0028`): committed and pushed -
[decision](decisions/tmf-api.md#the-last-gaps-2026-10-18),
[suspension](decisions/tmf-api.md#suspension-2026-10-19),
[alteration prices](decisions/catalog.md#alteration-price-types-2026-10-19). The
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
Decision: [tmf-api.md](decisions/tmf-api.md#product-writes-through-orders-2026-10-17).
Deployed with `d499338`.

**Event notifications** (2026-10-17, approved plan: three slices, all 17 `/hub`
operations, real delivery). Slice 1: hubs in every API, callbacks https-only to public
addresses outside `APP_ENV` dev/test (the VPS runs `staging`), migration `0017`. Slice 2:
transactional outbox (`0018`), events from every TMF write, orders, bills and the worker,
each payload validated against its listener schema. Slice 3: the worker's step 6 POSTs
due deliveries (retries 1 min ... 12 h, then failed), re-checks and pins the address
before each send, purges after 30 days. Coverage 122/129 (94.6%). Decision:
[events.md](decisions/events.md). Deployed with `d499338`: the worker makes outbound
HTTPS calls to hub callbacks (none until a hub exists).

**Another 10%: deletes and order cancellation** (2026-10-17, approved plan: two slices,
13 operations, no `/hub`; cancel failures are a 409 with nothing stored; the UI gets an
"Undo deactivation" for products pending termination). Slice 1 (9 operations, no
migration): DELETE on individual, organization, customer, billingAccount,
financialAccount, partyAccount, settlementAccount, billFormat, billPresentationMedia
for entries nothing references (a billing account: no history at all); writers take
what they reference FOR KEY SHARE, the worker skips a deleted account. Coverage 101/129
(78.3%). Decision: [tmf-api.md](decisions/tmf-api.md#deleting-parties-customers-and-accounts-2026-10-17).
API notes added with the human's OK. Committed and pushed (`656c7fc`). Slice 2 (4
operations): cancelProductOrder undoes a completed order before any of it takes effect
(409, nothing stored, otherwise), DELETE removes rejected orders, migration `0016`
`cancel_product_order` + order cancellation columns, UI "Undo deactivation". Coverage
105/129 (81.4%). Decision: [tmf-api.md](decisions/tmf-api.md#order-cancellation-and-deleting-rejected-orders-2026-10-17).
Deployed with `d499338`.

**TMF620 completion** (2026-10-16, approved plan: two slices, 13 operations, no `/hub`).
Slice 1 (catalog deletes, 5 operations): DELETE on specification, offering, price,
category and catalog for entries nothing references (D1 narrowed to TMF620); orders now
hold their offerings `FOR SHARE`. Coverage 84/129 (65.1%). Decision:
[catalog.md](decisions/catalog.md#deleting-catalog-entries-2026-10-16). Slice 1
committed and pushed (`284ada1`). Slice 2: importJob/exportJob (8 operations),
migration `0015` `catalog_job`; documents inline as `data:` URLs both ways (no download
endpoint - a change from the plan, because the spec's ExportJob_FVO requires `url`);
import under POST's rules, all or nothing. Coverage 92/129 (71.3%). Decision:
[catalog.md](decisions/catalog.md#import-and-export-jobs-2026-10-16). Deployed with
`d499338`.

**TMF666 accounts** (2026-10-16): two approved slices, both committed. Slice 1:
financial accounts (4 operations) owned by a party, billing accounts linked via
`financialAccount`, per-currency balance, read-only UI screen; migration `0013`. Slice 2:
party and settlement accounts (8 operations), records only (no ledger), linkable to the
same owner's financial account; migration `0014`. TMF operation coverage: 79/129
(61.2%); scoped API items: 95. Decision: [accounts.md](decisions/accounts.md).
Deployed with `d499338`. API notes for these resources and later account attributes
were completed locally in the approved 2026-10-20 cleanup; API references regenerated.

**Refunds and bill delivery** (2026-10-16): approved milestone adds 14 TMF operations:
refunds, on-demand bills, bill formats and presentation media. Migrations `0010`-`0012`
add six tables, payout credit consumption, off-cycle documents and presentation
snapshots. Decision: [billing-completion.md](decisions/billing-completion.md).
TMF operation coverage: 67/129 (51.9%); scoped API items: 83. On-demand bills wait for
the next successful account run to see the request day's orders. Formats are detailed
or summary JSON; media are preferences, not external delivery. The documentation
script edits were approved and applied by the human. Commit, push and a deployment
attempt were separately authorized on 2026-10-16.

**TMF620 catalog organization** (2026-10-16): local feature slice adds category and
product catalog list/retrieve/create/PATCH (eight operations), hierarchy validation,
root-category catalog membership and category/offering navigation. Migration `0009`
adds four tables, leaving existing offerings unclassified. Decision:
[catalog.md](decisions/catalog.md#categories-and-product-catalogs-2026-10-16).
TMF non-listener operation coverage: 53/129 (41.1%); scoped API items: 69.
Included in the commit, push and deployment-attempt approval of 2026-10-16.

**TMF input/read completeness** (2026-10-14): one local slice implements POST type
checks, strict write queries/fields, explicit errors for unsupported comma filters,
product/order monthly price views, customer/account and product navigation, and
read-only bill cycles with bill links, next dates and tax-free totals. Migration
`0008` snapshots prices for new completed add items; old orders keep unknown quotes.
Decisions: [tmf-api.md](decisions/tmf-api.md#input-correctness-and-read-completeness-2026-10-14).
New tests: `tests/test_api_read_completeness.py`; progress scope is now 59 API items.
Committed; deployed with `d499338`.

**MCP connector** (task of 2026-10-11, [docs/mcp.md](mcp.md)): five slices, each
committed after the human's OK and then pushed; nothing deployed without a separate
approval. Implemented and deployed: slice 1 (server, static tokens, scopes, limits, audit;
`6ae9f40`), slice 2 (OAuth 2.1 + DCR + discovery + login page; `15674e0`), slice 3 (57
tools generated from `app.openapi()` + drift check; `db765f1`), slice 4 (`compose.prod.yaml`
service, deploy.sh checks, `scripts/mcp-smoke.sh`, vps-infra change request; rehearsed
with Caddy 2.11.4, subsequently deployed; `5f2e436`), slice 5 (client setup docs, token tooling
`python -m bss_mcp.tokens` + `make mcp-token*`). All slices done; the production
connector was verified by the human with Dify, Claude, ChatGPT and others (2026-10-19).

**Current backlog (reconciled 2026-10-20):**

Documentation/backlog/deployment-identity cleanup is complete locally: corrected
TMF620/666 notes and regenerated references; reconciled release, authentication and
feature status; implemented the running-commit marker. Validation: `make lint`,
`make test` (1,405 passed, 559.53 s), `make decisions-check`, Bash syntax and four
isolated marker checks pass. A second `make docs` produced identical content across
all 53 generated pages. No scope changes, commit or deployment in this task.

1. **Deployment identity rollout:** the approved deploy.sh change is implemented
   locally: atomically record the full running commit in `~/bss/DEPLOYED` after
   service health, read it back and print it in the remote smoke. The human applied
   the protected-file patch. Production activation awaits a separately approved deploy.
2. **Progress tracker:** db/API/worker and vitest coverage collection is implemented.
   Remaining: progress-report drift enforcement in the pre-commit/release gates and
   per-item deployed status from `docs/progress/deployed.txt`. The remote identity
   marker above does not implement per-item deployment coverage.
3. **Remaining Phase 2** (APP_PORT, localhost:8080 references, stopping all profiles
   with make down) remains deferred; exact acceptance criteria are in Open checks.
4. **Backport vps-lab guard changes** (ports 8444 and 2053) remains parked; the push
   policy is done. Harness edits require a reviewed diff and human approval.
5. **Cloud startup/resume validation:** still unproven in an actual supported client;
   retain the hook/environment/egress checks described in `docs/cloud.md`.
6. **Subscriber-card visual review:** no completion evidence is recorded for the
   requested human review (figures, bill dialog, Record payment, New subscriber, Edit).
   Retained as a manual follow-up, rather than a prerequisite for releases already shipped.
7. **UI vs MCP gap** (2026-10-20): slice 1's design (refunds and on-demand bill)
   was approved and is implemented locally. Three full backend passes (1,406 each,
   dev/CI/dev), 277 frontend tests in each timezone, five browser smokes, backend and
   frontend lint and decisions check passed. Both negative proofs failed as expected
   and were reverted. Progress generation passed (1,406 backend tests with the two
   existing collector warnings; 277 UI tests); all four new UI items are implemented
   and tested. `docs-check` regenerated the existing TMF620/666 cleanup correctly but
   still fails against the uncommitted generated API pages. No commit or deployment
   in this task. Slice 1 adds four API operations to the UI's original 40 of MCP's 145.
   Remaining slices (modify,
   suspend and resume; order history; product specifications) still need design OKs.
   Design and scope: [docs/ui-mcp-gap-plan.md](ui-mcp-gap-plan.md).

**Completed backlog milestones:** the worker including Billing runs UI, bill
lifecycle and subscriber create/edit shipped with `d499338`; migrations through
`0030` are deployed. The docs task has delivered the database reference, API registry
and runtime architecture in [docs/overview.md](overview.md#runtime-architecture).
MCP connector slices 1-5 and review slices A-C are deployed (A-C: `40805f4`, included
in `7ad101e`). The MCP review still has the dated production restore check and
historical test-data observation in [docs/mcp-e2e-review.md](mcp-e2e-review.md).
`BILL_PAYMENT_TERM_DAYS` wiring and U11 are complete. Interactive `/docs`, `/redoc`
and `/openapi.json` are disabled (`app/main.py`, `test_no_interactive_api_docs`);
in-process OpenAPI remains available to tooling. No scope items have changed here.

Also pending, not ordered: the two fixes to port to vps-lab-template
([infra.md](decisions/infra.md#to-port-to-vps-lab-template)), and the open design
question of changing `billCycleDay`
([billing-core.md](decisions/billing-core.md#open-questions); suspension semantics
closed 2026-10-19).

## Open checks

Nothing below is done; remove an item only when it has passed (or, for work items,
shipped).

- **Network isolation remains open:** the app shares `shared` and binds VPS loopback
  127.0.0.1:18081. Since UI user management, application authentication protects
  business routes on those paths too; health probes remain public. A private app/MCP
  network and a Caddy-side alias remain a separate infrastructure task.
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
- **DB connections: pooled (2026-10-20), confirm on the VPS.** The API takes
  connections from a pool of at most 10 (`DB_POOL_SIZE`) instead of opening two per
  request; this also cut `make test`. To confirm after the deploy: at most 10 bss API
  sessions in `pg_stat_activity` under load, and the app answers after the nightly
  reboot. [infra.md](decisions/infra.md#db-connection-pool).
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

