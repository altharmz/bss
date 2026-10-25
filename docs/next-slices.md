# Next slices: finish prepaid, then MCP parity in the UI (planned 2026-10-24)

This tracker follows the prepaid tracker ([prepaid-slices.md](prepaid-slices.md), all
rows done):
- **Phase A** finishes prepaid.
- **Phase B** gives the UI every operation MCP has.

This file says **which slice is next and when it is done**. The full design of every
slice, with all decisions made, is in [next-slices-design.md](next-slices-design.md)
(section N = slice N). The human delegated every design decision on 2026-10-24. **No
slice needs approval: build what the design says.**

## How to run a slice

- Read CLAUDE.md, then "Rules for every slice" and section N of the design, and the
  code it names.
- One commit per slice: code, tests, generated docs, the decision entry, and this row's
  status (`done: <commit subject>`). The pre-commit hook is the one full test run.
  Commit and push, and never deploy.
- New tests must fail without the slice. Never weaken an existing test (the TEST-CHANGE
  rule).
- If the code contradicts the design, follow "When the spec is wrong" in the design.
  Stop only for an unplanned migration or a billing change.
- If a slice is too large, split it into `Na`/`Nb` rows here, each committed separately.
  Do not widen its scope.

Status: `todo` · `next` · `done: <commit subject>` · `split`

## Phase A: finish prepaid

| # | Slice | Kind | Done when | Status |
|---|---|---|---|---|
| 1 | Prepaid first-charge preview in Add product | UI | The prepaid preview shows to pay / available / shortfall, with a Top-up handoff. Postpaid is unchanged. | done: Show the prepaid first-charge preview in Add product with a Top-up handoff |
| 2 | Balance action history on the card; transactions include balance actions | backend (bug fix, internal contract) + UI | The newest `balanceAfter` equals the ledger on a prepaid account with adjustments and transfers. The history panel lists sent and received actions with a detail dialog. | done: Show prepaid balance actions on the card and include them in transactions |
| 3 | Cancel top-ups and adjustments, release holds, remove refused actions, edit notes | UI | The four PATCH bodies and the DELETE are pinned and replayed. A 409 is shown in place. | done: Cancel top-ups and adjustments, release holds, remove refused actions, edit notes in the Balance action dialog |
| 4 | Product actions: suspend, resume, end on a date; pending changes with Undo | backend (internal `pendingChanges`) + UI | Bodies for both modes are pinned and replayed. Pending changes show and can be undone. | done: Add product actions and pending changes: suspend, resume, end on a date |
| 5 | Change offering (both modes) | UI | Postpaid any date from today. Prepaid fixed at `max(paidThrough, today)`. Pinned and replayed. | done: Add Change offering to product actions, both modes |
| 6 | Prepaid commands screen | UI | List, filters, detail, and Retry only for `failedTechnical`, pinned. Sidebar badge. | done: Add the Prepaid commands screen with filters, detail, Retry and sidebar badge |
| 7 | Prepaid browser check and release evidence | e2e | `make web-e2e` passes the prepaid steps. prepaid-release.md is updated. | done: Add the prepaid browser check to the e2e smoke and update the release evidence |
| 8 | Prepaid dated suspend/resume and number-only changes | backend + UI | A dated prepaid status change completes on its date through the worker. Number-only changes work. The UI date is editable. | done: Apply dated prepaid suspend/resume through the worker and allow number-only prepaid changes |

## Phase B: every MCP function in the UI

| # | Slice | Kind | Done when | Status |
|---|---|---|---|---|
| 9 | Sidebar sections and global Orders screen | UI | As in the 2026-10-21 decision, plus the design's additions. | done: Add sidebar sections and the global read-only Orders screen |
| 10 | Order writes: cancel, drafts, order details | UI | Cancel, edit details, save/edit/submit/delete drafts, all pinned and replayed. | done: Add order cancel, details and draft writes to the order dialog |
| 11 | Product specifications | UI | A spec can be created inside New offering and used at once. CRUD is pinned. | done: Add product specifications tab with create, edit and delete |
| 12 | Offering and price detail, edit, delete | UI | Edits are pinned. Refusals are shown. The code edit keeps other identifiers. | done: Add offering and price edit, end and delete to the offering dialog |
| 13 | Categories and product catalogs | UI | The tree, membership and catalogs are pinned. Cycles are excluded. | done: Add categories and product catalogs tabs to the Catalog screen |
| 14 | Catalog import and export jobs | UI | An export, then its import, round-trips. Download works. | todo |
| 15 | Parties and customers | UI | Parties without accounts are reachable. Deletes are pinned. | todo |
| 16 | Billing account delete; financial account writes and linking | UI | Create, edit, link, unlink and delete are pinned and replayed. | todo |
| 17 | Party and settlement accounts | UI | CRUD for both is pinned. | todo |
| 18 | Billing settings: cycle specs, bill formats, presentation media | UI | CRUD and the `billStructure` selection are pinned. | todo |
| 19 | Payments and refunds history | UI | The card panel and the global screen are pinned. | todo |
| 20 | Global bills, bill cycles, rates | UI | The list, filters, cycle and rates views are pinned. | todo |
| 21 | Product and number inventory | UI | All product states are reachable. Number delete is pinned. | todo |
| 22 | Balances screen (TMF654 reads) | UI | Every TMF654 list and GET is used. Details reuse slice 2/3. | todo |
| 23 | Event subscriptions (hubs) and System panel | backend (internal list + MCP tool) + UI | Hubs are listed, created and deleted for v4 and v5 APIs. The status panel renders. | todo |
| 24 | Parity check | test | The test fails when an MCP operation has no UI call and no allowlist entry. The gap plan is closed. | todo |

Not planned: moving an account with history between postpaid and prepaid; the
subscriber-card visual review (a human check, kept in the CLAUDE.md backlog).

## Estimate

| Date | Remaining | Note |
|---|---|---|
| 2026-10-24 | 24 rows, expect 28-32 commits | Past UI slices split about 1.2× (P6c became 2). Likely splits: 2 (backend/UI), 4 (backend/UI), 8, 10, 23. |
| 2026-10-25 | Phase B: 16 rows (9-24) | Phase A: 8 rows in 8 commits, no splits (slices 2 and 4 stayed one commit each): 9a3dab8, 46bd515, e7bdbbf, 90dd631, 1f35502, 11e8b26, accf340 and the slice 1 commit 5492db3. Deviations: Update schemas require `status` (and a hold's `reason`) in 3; offerings without a recurring price are not offered in 5; the commands API filter is `billingAccount.id` in 6. TEST-CHANGE in 5, 6 and 8. The worker heartbeat timing test failed once under load in 7 and 8 (passes alone). |
