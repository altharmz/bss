# UI/MCP parity slices: design record

The design of the 24 slices that brought the admin UI to parity with the MCP operations.
All are built; the slice numbers are cited by code comments and decision entries. Each
entry keeps what is not obvious from the code; behaviour per screen is in [ui.md](ui.md),
the decisions by area in [decisions/](decisions/README.md) (the file named in each entry).
Decided by TMF where TMF defines something, otherwise by the existing decisions.

## Rules every slice followed

- **The server decides.** The UI hides an action only when the data it shows makes the
  refusal certain; otherwise it offers it and shows the server's 409/422 message in the
  dialog, keeping the entered values. No client copy of business rules.
- **Writes are never retried automatically.** TMF654 POSTs and the order POST carry an
  Idempotency-Key. After an uncertain PATCH/DELETE the dialog says "Unsure whether it
  was saved: Refresh to check".
- **Dates** are business dates in `BILLING_TZ`, sent as `zonedDateTime` where an instant
  is required; date inputs default to the business date, never the browser's.
- Every new screen, action and list has a `data-ui` marker, a `docs/progress/scope.yaml`
  item and a line in `ui.md`. Backend parts change `spec/internal/bss-internal.oas.yaml`
  first, then the code, `make docs` and `bss_mcp/operations.py`.
- Out of scope everywhere: new dependencies, new browser storage, changes to `spec/`.
- If the code contradicts a fact here, follow the code and the TMF spec and record the
  deviation; stop only if a migration or a billing change would be needed.

## Phase A: prepaid

### 1. Prepaid first-charge preview in Add product

`GET /internal/v1/billingAccount/{id}/firstCharge` on a prepaid account returns `required`,
`available`, `shortfall`, `amount` (interval with taxes) and `balanceAfter`; nothing is held
until the start date. The preview shows **To pay now** (**To pay on {date}** for a future
start), Available, Shortfall (only when > 0) and Ledger after (start today only). A
shortfall for a start today is a warning with a **Top up {shortfall}** button (closes Add
product, opens Top-up prefilled; no nested modals); for a future start only a hint ("funds
are checked on {date}"). Submit stays enabled; the stored rejection `insufficientFunds`
shows through the rejected-order path. Postpaid preview unchanged. Decisions: frontend.md.

### 2. Balance action history; transactions include balance actions

Bug fix, no migration: the card's transaction history (`page_account_transactions`) now has
arms for adjustments (completed or cancelled), reversals and completed transfers in/out, so
the newest `balanceAfter` equals the ledger `account_balance`; holds never change the ledger
and stay out; fees are already ABRs. `Transaction.type` gains `adjustment`, `reversal`,
`transferIn`, `transferOut`. UI: a **Balance actions** panel on prepaid cards (TMF654
`balanceActionHistory` by `partyAccount.id` merged with `receiverBucket.id`, de-duplicated,
newest first) with a detail dialog; reloaded when the card's `version` bumps. Decisions:
frontend.md, prepaid.md.

### 3. Cancel top-ups and adjustments, release holds, remove refused actions

In the balance-action detail dialog, with an inline confirm: **Cancel top-up** (completed,
unspent and unheld), **Cancel adjustment**, **Release hold** (operator-owned `created`
reservation), all `PATCH {status: cancelled}`; **Remove** (`DELETE`, only `failed`, any of
the four kinds); **Edit note** (`reason`/`description`). Transfers cannot be cancelled. A
409 shows in the dialog. Decisions: frontend.md.

### 4. Product actions: suspend, resume, end on a date (both modes)

Backend, no migration: `AccountProduct.pendingChanges[]` (`kind` status|offering, `date`,
`status`/`productOffering`, `productOrder` link) lists status changes with day > today
(prepaid: pending dated `order` commands) and offering changes not yet in effect (postpaid:
day > today; prepaid: `effect_announced_at IS NULL`), ordered by date, always present.
UI: the row's single Deactivate becomes an **Actions** menu: Suspend (active, no pending
status change), Resume (suspended, cause not `funding`/`account`), Change offering, End on a
date, Deactivate. One dialog posts a TMF622 `modify`/`delete` item with `requestedStartDate`
at 00:00 `BILLING_TZ`. Suspension does not change billing ("charged by its dates"). A
funding-suspended product shows the needs-top-up text and a Top up link instead of Resume.
Each `pendingChanges` entry is a row line ("Suspends on …", "(waits for funds)" when dated in
the past) with **Undo** (`cancelProductOrder`). Decisions: frontend.md, tmf-api.md.

### 5. Change offering (both modes)

`modify` item with `productOffering` and never a `status`. Offered for active/suspended
products with no end and no pending offering change (prepaid also needs `paidThrough`).
Offerings come from the Add product source minus the current one. Postpaid: date defaults to
`nextChargeDate`, any date from today; the old offering is billed to D-1, the new from D, a
prorated refund adjusts an already billed period; the preview is
`firstCharge?productOffering=&startDate=`. Prepaid: the date is fixed to
`max(paidThrough, today)`; the change applies at the next renewal and only if the balance
covers the new interval. Decisions: frontend.md.

### 6. Prepaid commands screen

`/prepaid-commands` under Billing: `GET /internal/v1/prepaid/command` (pages of 50) with
state and `?account=` filters; columns id, kind, account, state, due, attempts, next retry,
result. The detail dialog shows every field and the `needs_topup` block; **Retry** appears
only for `failedTechnical` (`POST …/retry`). A red badge counts `failedTechnical`
(`?state=failedTechnical&limit=1`, `X-Total-Count`). The card links to it. Decisions:
frontend.md.

### 7. Prepaid browser check and release evidence

A Chromium smoke test (`frontend/e2e/smoke.spec.ts`) on the seeded stack: Pavel Orlov's card
(KPIs, balance actions, a detail), a 10.00 cash top-up, Add product with the prepaid preview,
Vera Lebedeva's funding-suspended product with the Top up link, the Prepaid commands list.
It leaves data changed, which is accepted because the e2e runs against a disposable stack.

### 8. Prepaid lifecycle parity in the backend (dated status changes, number changes)

TMF622 gives every item `requestedStartDate` and postpaid honours it, so prepaid refusing
dated changes was a gap. A dated suspend/resume (D > today) on a prepaid account keeps the
order and item `inProgress` with one `order` command due on D (no migration). Acceptance
rules are the same-day P3f rules evaluated for D: one waiting status change at a time and a
delete refused while one waits (`openModifyItem`), a dated change refused while a
termination is set (`productAlreadyEnded`), `statusUnchanged` checked against the status
that will hold on D. On D the billing owner runs the same-day execution (suspend sets the
manual cause and keeps the paid interval; resume lifts it and queues the renewal after
expiry); if the product ended or the account is no longer `Active`, item and order end
`failed` with `productAlreadyEnded`/`accountNotActive` - the first use of TMF622 `failed`.
`cancelProductOrder` before D cancels the command and order; on D it is the postpaid
same-day undo; later a 409. A number-only `modify` is accepted on prepaid exactly as on
postpaid (synchronous, no money, no command). Moving an account with history between
postpaid and prepaid is issue #150.
Decisions: prepaid.md.

## Phase B: the rest of the operations

### 9. Sidebar sections and the global Orders screen

As in [frontend.md](decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21).
Billing order: Billing runs, Prepaid commands, Financial accounts. A prepaid `inProgress`
order links to its command; the card's Orders link goes to `/orders?account=`.

### 10. Order writes: cancel, drafts, order details

Supersedes "no writes" of the Orders screen. In the order dialog: **Cancel order**
(`cancelProductOrder`, optional reason; completed, draft and prepaid inProgress), **Edit
details** (PATCH `description`, `priority` 0-4, `category`; `externalId` stays API/MCP),
drafts (Add product's **Save as draft** sends `requestedInitialState: "draft"`; **Submit**
PATCHes `state: acknowledged`; **Edit draft** only for exactly one `add` item, others say
"Edit this draft through the API"; **Delete** also for `rejected`). The list has a `draft`
state filter. Decisions: frontend.md.

### 11. Product specifications

Catalog tab: list with offering counts, New/Edit (simple fields; lifecycle forward only),
Delete (hidden when a loaded offering uses it), and **New specification…** from New
offering with the result preselected. Characteristics stay API/MCP. Decisions: catalog.md.

### 12. Offering and price detail, edit, delete

Offering detail from `GET productOffering/{id}`; Edit PATCHes changed fields (including
`validFor` and the `offeringCode` entry of `externalIdentifier`, keeping other identifiers);
Delete only when not Active/Retired, without products or prices ("retire it instead" on 409).
Prices: detail, Edit, End price (`validFor.endDateTime` tomorrow or later), Delete for
unattached or never-ordered; a live offering's price change goes through the internal
priceChange. Decisions: catalog.md.

### 13. Categories and product catalogs

Categories tab (tree by `parent`, parent select excludes itself and descendants, forward-only
lifecycle, membership writes the category's whole `productOffering[]`, delete without
children/offerings) and Catalogs tab (root categories only; delete always offered).
Decisions: catalog.md.

### 14. Catalog import and export jobs

Jobs are synchronous with the document inline as a `data:application/json;base64,…` URL,
1 MB cap. Import/export tab lists both kinds merged newest first. Export scope is everything
or `productCatalog.id=<id>`; on `Succeeded` Download decodes to `bss-catalog-{date}.json`.
Import takes a `.json` file or pasted text, checks JSON and size, shows a failed `errorLog`
in full. Deleting a job removes only the record. No polling. Decisions: catalog.md.

### 15. Parties and customers

`/parties` with tabs Individuals, Organizations, Customers (`getAll`, client-side name
search). The detail edits through the subscriber form pieces; Delete is hidden when the
loaded data shows a dependency, the server's 409 still shows. Parties and customers without
an account are reachable here. Decisions: frontend.md.

### 16. Billing account delete; financial account writes and linking

**Delete subscriber** in the card menu: postpaid only, and only with no products, charges,
bills, payments or transactions loaded (prepaid never: the bucket is history). Financial
accounts: New/Edit/Delete (delete when `accountRelationship` is empty; the owner is
`relatedParty` role `owner`, refused to change while accounts link). The subscriber Edit
form links a financial account of the owner (`financialAccount` ref or `null`).
Decisions: accounts.md.

### 17. Party and settlement accounts

The screen becomes **Accounts** (route stays `/financial-accounts`) with tabs Financial,
Party, Settlement; the last two have New/Edit/Delete with a financial-account link and no
balance column; delete always offered. Decisions: accounts.md.

### 18. Billing settings: cycle specifications, bill formats, presentation media

`/billing-settings`, three tabs. The built-in `monthly` cycle specification is read-only;
stored ones edit `name`/`description` only (policy fixed monthly, shift 0). The subscriber
Edit form writes cycle specification, bill format and presentation media through
`billStructure`. Delete hidden while loaded accounts use it. Decisions: accounts.md.

### 19. Payments and refunds history

A **Payments** panel on the card (`GET /payment?account.id=`, with detail) and `/payments`
with tabs Payments | Refunds (status filter, account chip, client-side date range).
Decisions: frontend.md.

### 20. Global bills, bill cycles and rates

`/bills` (filters state, account chip, `billingDate`; columns bill no., account, billing
date, state, amount due, kind onCycle/offCycle), the existing bill dialog plus links to the
Bill cycle and the applied customer billing rates (table of period, product, amount, tax),
and a Bill cycles tab. Decisions: bills.md.

### 21. Product and number inventory

`/products` over every status (status and account filters server side, offering and number
client side) with a TMF637 detail linking to its orders and realizing resource. Numbers:
detail dialog and **Delete number** for a never-assigned number. Writes to products stay
orders. Decisions: frontend.md.

### 22. Balances screen (TMF654 reads)

`/balances` with tabs Buckets (joined with accumulated balances), Top-ups, Adjustments,
Holds, Transfers (status filter, `partyAccount.id` chip); a row reuses the slice 2/3 detail
with its actions. Postpaid accounts never appear. Decisions: frontend.md.

### 23. Event subscriptions (hubs) and System panel

TMF has POST/DELETE `/hub` per API and no list, so an operator view needs one internal read
operation (not a TMF extension): `GET /internal/v1/eventSubscription` (live hubs newest
first, `offset`/`limit`, `api` filter) and the MCP tool `list_event_subscriptions`. UI
`/integrations` under a fourth sidebar section **System** (with Users for admins): list, New
subscription (API, callback, event types from a list in `frontend/src/api/hubs.ts` compared
with `app/events/types.py` by a test; `query = eventType=A,B`), Delete, the server's 422 for
a non-public callback shown as is; a System panel with `/billing-status`, `/healthz` and a
link to the `BssBillingAccount` schema. Visible to every role. Decisions: events.md,
internal-api.md.

### 24. Parity check

`scripts/ui_mcp_parity.py`, run by `tests/test_ui_mcp_parity.py`, lists the MCP operations
(`api_operations(app.openapi())`), extracts every (method, path template) the frontend
calls outside tests and generated code (`${…}` normalised to `{}`), and fails listing each
operation with neither a UI call nor an entry in `tests/ui-mcp-allowlist.txt` (one line
with its reason, e.g. `create_product`/`patch_product`/`delete_product`: orders; `get_bill`:
the internal bill detail). Decisions: harness.md.
