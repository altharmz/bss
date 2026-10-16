# Frontend (admin UI, stage 5): decisions and conventions

The refund/bill-delivery milestone adds the ledger type `payout`, displayed as
“Refund paid out”. The existing `refund` label still denotes an ABR credit.
Refund and presentation management remain API/MCP workflows in this milestone.

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md). Screen-level detail:
[docs/ui.md](../ui.md). Deploying the UI: [infra.md](infra.md#deploying-the-ui-stage-5-checkpoint-7).

## Frontend (stage 5) decisions

- **A subscriber row is a billing account** (it has the balance, cycle day and products).
  Name from the customer; the status pill and filter are the account's real `state`
  (Active / Suspended / Closed), which is also what ordering checks (`accountNotActive`).
  **A customer with two accounts shows as two rows.**
- The subscribers list makes no per-row requests: one billingAccount list (balance
  included) and one product list (`status=active`), grouped client-side, page by page.
- "Registered" is replaced by "Cycle day" (no creation date in TMF629/666, and
  `created_at` is the database's `now()`, not the injected clock).
- **Subscriber create and modify** (`screens/SubscriberForms.tsx`, `cloud/subscriber-edit`;
  details in `docs/ui.md`), through the existing TMF routes - no backend change. **There
  is no delete subscriber operation** (confirmed by the human, 2026-10-10): accounts carry
  products, bills and payments; an account can be Closed. New subscriber = party, then
  customer (status Active), then billing account; Edit = merge patches of only what
  changed, party -> customer -> account. A failed step names what already exists or was
  already saved; nothing is undone. Cycle day and currency stay fixed. Bodies pinned on
  both sides (component tests, `test_ui_requests.py`); the e2e opens both dialogs
  read-only. Negative proofs: every account attribute always patched, the customer
  created without `Active`. The card header carries Edit, Record payment (bill
  lifecycle slice 4) and Add product side by side.
- Credit limit: shown, and the over-limit warning in Add product, informational only.
- Offering code: `externalIdentifier` (above). Subscriber no. and `BA-xxxxxxxx` are
  display ids: the first 8 hex digits of the UUID, upper case.
- Weekly recurrence is not offered (monthly only). One currency in the UI (`UI_CURRENCY`);
  the backend stays multi-currency.
- Pills for statuses the design lacks (`created`, `pendingTerminate`, `suspended`,
  `cancelled`, Suspended/Closed accounts) reuse existing token colours only.
- Retiring an offering does not end its prices; its products keep billing.
- **Billing runs screen** (stage 4 slice g, `/billing-runs`, `screens/BillingRuns.tsx`;
  details in `docs/ui.md`). The stage 4 note wins over the handoff (Q11): "Run manually
  for a cycle date" is **Catch up to** a date (default and max = the due date: today
  from `BILLING_RUN_AT` on, else yesterday - Q5), "Re-run" is **Retry**, offered on
  **every** failed run (Q3: banner, history row, run dialog). A gap the worker will not
  bill on its own (latest run older than the day before the due date, Q4) gets a note.
  The run time shown is `BILLING_RUN_AT` (00:30) from the context. A run opens a dialog
  with its per-account outcomes (failed / blocked / ok). The list polls every 5 s while
  a run is running or a request queued from the screen is not carried out yet -
  carried out = a run started at or after the request's `requestedAt` (server clock,
  never the browser's) has finished; proven against the real worker in
  `tests/test_ui_requests.py`. It gives up after 3 minutes with a note. The sidebar's
  Billing runs item shows the run count, or the red "failed" badge when the latest run
  failed, and is reloaded when the screen sees the latest run change.
- Charge timing copy follows the end-of-day rule: an order completed today is charged by
  the run of max(start, tomorrow) and billed on the next cycle day from there - nothing is
  "charged on confirmation".
- Smoke on the VPS: a separate basic-auth user `smoke` (own hash), GET-only in Caddy
  (any other method 403); its plaintext lives only in the local `.env.smoke`, never
  shipped and never printed.
- Pre-commit runs `make web-lint` and `make web-test` only when staged files touch
  `frontend/` or `spec/`. **A change in `app/api` or `app/internal` can still break the
  UI** without touching either; `make verify` (which runs `web-e2e`) is the gate that
  catches it.

"Offering code: `externalIdentifier` (above)" refers to the TMF620 mapping in
[catalog.md](catalog.md#tmf620-mapping).

## Frontend conventions

The admin UI (stage 5) lives in `frontend/`: Vite + React + TypeScript, built to static
files and served by the shared Caddy on :8445 (same origin as the API, same basic auth -
no CORS). It never enters the backend image (`.dockerignore`). `design-ref/` is the design
handoff: a pristine reference, never edited (settings.json deny + guard.sh). Types come
from `spec/` (TMF and `spec/internal/`), never from `/openapi.json`. Fonts are
self-hosted (`@fontsource`), no CDN at runtime. Every dependency needs a stated reason:
`package.json` carries them under `"//"`.

- **Node runs only in a container, never on the host.** Two compose services (local
  only, `profiles: [web]`), same image (`node:22-alpine` pinned by digest), same mounts
  (`./frontend` at /web, `./spec` read-only, `node_modules` and the npm cache as named
  volumes - the host's `frontend/node_modules` is only the empty mount point):
  - `web` has network: `make web-dev` (Vite on 0.0.0.0 inside, published on
    127.0.0.1:5173, proxying `/tmf-api` and `/internal` to `app`) and `make web-install`;
  - `web-offline` has `network_mode: none`: build, lint, test, types, fmt.
- **Installs are explicit.** Only `make web-install` downloads (`npm ci`, exactly the
  lockfile) and stores the lockfile's hash in the volume. Every other target first runs
  `frontend/scripts/check-deps.sh`, which only compares that hash and fails with "run make
  web-install" - never an implicit install, in particular not from the pre-commit hook.
  Proven: `make web-build` succeeds offline (and the container cannot reach the
  registry); a changed lockfile makes `make web-test` fail with the hint.
- **npm hardening** (`frontend/.npmrc`): `ignore-scripts=true` (no dependency runs code
  at install time; nothing in the tree needs it), `save-exact=true`, no audit/fund calls.
  TypeScript is 5.x on purpose: `openapi-typescript` requires `^5` and `typescript-eslint`
  `<6.1`, and npm enforces peer ranges.
- **Generated types** (`frontend/scripts/gen-types.mjs`, openapi-typescript) for TMF620,
  622, 637, 666 and the internal API go to `src/api/generated/`, which is git-ignored
  (~1 MB) and regenerated by every build, lint and test run - it cannot go stale.
- **Money in the UI:** amounts become integer cents at the edge (`src/format/money.ts`);
  the UI never does arithmetic on binary floats, and typed amounts are sent as the
  validated decimal string. Negatives use U+2212. Dates are business dates in BILLING_TZ
  (from `/internal/v1/context`) via `Intl`, never the browser's zone.
- **Dates the UI sends** (requestedStartDate at noon, validFor at midnight) carry
  BILLING_TZ's offset **for that date** (`zonedDateTime`: `Intl.DateTimeFormat` with
  `timeZone`, so a DST zone gets the right offset either side of a change) - never the
  browser's offset, and not `/internal/v1/context`'s `utcOffset`, which is the offset at
  *now* (top bar only). `npm test` runs the suite twice, in UTC and with
  `TZ=America/New_York`, so every request body is proven independent of the browser's
  zone; a local-`Date`-constructor bug passes the UTC run and fails the second one.
- **Harness:** `settings.json` denies `pnpm`, `yarn`, `corepack` on the host and edits
  under `design-ref/` (`npm`, `npx`, `node` are not denied there: a deny rule cannot
  depend on cloud mode, see ["Agent guardrails"](harness.md#agent-guardrails-what-they-are-for)); `guard.sh` blocks a command that runs a Node
  tool on the host (also via `sh -c`, `xargs`, substitutions) unless it runs in one of the
  compose services `web`, `web-offline`, `web-preview`, `e2e` (or another container), and
  shell writes into `design-ref/`. It is quote-aware, so `docker compose run web-offline
  sh -c '... && npm ...'` and text mentioning npm in quotes pass; a heredoc still goes
  through the old raw-text checks. The format hook runs prettier (in `web-offline`) on
  edited UI files.
- **Structure** (`frontend/src/`): `api/` (fetch client: every failure is an `ApiError`
  whose message is the call, "GET /path → 503 Service Unavailable"; `getPage` requires
  `X-Total-Count`; `getAll` pages by 100; `useResource` = loading/error/ok + retry, never
  shows a stale or other key's result), `app/` (router, shell, system context and
  breadcrumbs), `ui/` (tokens, kit, `Table` on CSS grid, the four list states),
  `screens/`, `format/`. **Every list renders through `ListState`**: skeleton (8 rows
  sized by the table's columns), error (call + Retry), empty (copy + optional action),
  no results (Reset filters). The context (`/internal/v1/context`) loads before any
  screen: no date can be shown without the business date and zone.
- **Demo data: `make seed`** (`tests/seed.py`, also the e2e fixture). Everything goes
  through the public TMF API - never SQL - run in-process with a FixedClock that starts
  120 days before today and moves forward a day at a time: each day the billing run for
  that date (`bill_account` for every account, then `finish_run`) and then the day's
  orders and payments. The API refuses past-dated prices and orders, which is why the
  real clock cannot build history. Dates are relative to today, so future-dated and
  pendingTerminate products are really in the future. 4 offerings with codes (one In
  Design), 7 accounts (one Suspended, one Closed, several negative balances, credit
  limits), refunds, payments, bills. Refuses `APP_ENV=prod` and any database but the
  local `db`. Idempotent: if its catalog exists it does nothing; a partial seed is
  reported (reset with `make clean && make up`), never patched. Needs migrations at head
  (`make migrate`). `tests/test_seed.py` runs it on a throwaway database.
- **Catalog:** lifecycle steps (In Design -> Active -> Retired, Retired final) are made
  from an offering detail dialog (row click), with an inline confirm; the table has no
  room for an action column at 1280px. New offering = TMF620 price, then the In Design
  offering referencing it; the amount goes out as an exact JSON number token
  (`jsonWithAmount`), "Valid to" is inclusive in the UI and sent as the next midnight.
  **Change price** (offering dialog, only when there is an open-ended price in the UI
  currency) calls the atomic `priceChange` (D-W21); its copy states that existing products
  pick up the new price from their next billing period starting on or after the date,
  and new orders from the date.
- **The UI's write requests are pinned on both sides**: component tests assert the exact
  bodies, and `tests/test_ui_requests.py` replays the same bodies against the real API.
  Change both together.
- **Routes:** `/subscribers`, `/subscribers/{accountId}`, `/catalog`, `/billing-runs`
  (not `/billing`: never confusable with the app's `/billing-status`, which Caddy routes
  to the backend); `/` redirects to `/subscribers`; anything else is a not-found page.
- **Component tests stub every request** (`src/test/fetch.ts`): an unmatched one fails
  the test after it ends (`src/test/setup.ts`), not just renders a network error. The
  old behaviour made App.test "recovers on Retry" flaky (2 of 5 on master, 7 of 10 in
  the cloud): after Retry the subscribers list's unstubbed load raced the "no alert"
  assertion. The strict stub found 9 more under-stubbed tests. `pending` stubs a load
  the test does not care about; `NAV_RUNS`/`noRuns` the sidebar's run count.
- **`findBy`/`waitFor` wait up to 5 s** (`asyncUtilTimeout` in `src/test/setup.ts`;
  `testTimeout` 15 s in `vite.config.ts`, so a miss still reports the query's own error).
  Testing Library's 1 s default made Catalog's first test flaky ("no row Mobile L",
  doc-findings #19): a file's first render pays every cold cost (stubbed fetches, JSON,
  Intl, the table), and on a loaded workstation the single vitest pass of `make
  progress` took more than 1 s of wall clock for it. Proven (`cloud/quality-1`): delaying
  the catalog's price load by 1.2 s reproduced the exact error with the 1 s default and
  passed with 5 s; a 6 s delay still fails with "no row Mobile L" (a hang is still
  caught). Then 20/20 `npm test` runs (UTC and New York each) with four busy loops on
  the 4 cores. The timeout is a hang guard, not a speed assertion: no test waits for it
  to expire.
- **Polling tests** fake only `setTimeout`/`clearTimeout` and wait with an `until` helper
  (`BillingRuns.test.tsx`): Testing Library's `findBy`/`waitFor` hang under a fake
  `setTimeout`.
