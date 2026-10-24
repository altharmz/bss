# Next slices: finish prepaid, then MCP parity in the UI (planned 2026-10-24)

The tracker after the prepaid tracker ([prepaid-slices.md](prepaid-slices.md), all rows
done). It has two phases. **Phase A** finishes prepaid: the gaps the P6/P7 slices left
out on purpose, and the prepaid lifecycle that is still MCP/API only. **Phase B** gives the
UI every operation MCP has, so that no operator task needs MCP or the API.

Slices are numbered 1, 2, 3... in the order they should be built. Each one is planned as
one commit. This file lists **what** to build and **when it is done**. The design
decisions go into the matching `docs/decisions/*.md` file (usually
[frontend.md](decisions/frontend.md)) as each slice is built.

## How to run a slice (read first)

- Read CLAUDE.md, the decision file named in the slice, and the code it points to before
  you start. Re-measure the gap first: the operation list below was taken on 2026-10-24
  (`bss_mcp.tools.api_operations(app.openapi())` gave 184 MCP tools; UI requests are in
  `frontend/src/api/*.ts` and `frontend/src/screens/*.tsx`).
- **Frontend-only unless the slice says otherwise.** Every API operation listed already
  exists and is tested. If a slice turns out to need a backend, migration or data-model
  change, stop and propose a plan to the human first (CLAUDE.md).
- Slices marked **Design approval** need a short design note that the human approves
  before you write code. Put the note in the slice's decision file. The other slices follow
  the existing patterns and the decisions already recorded.
- Follow the [frontend conventions](decisions/frontend.md#frontend-conventions):
  - Pin write bodies on both sides: component tests and `tests/test_ui_requests.py`.
  - Money is a decimal string or integer cents (`format/money.ts`). Dates are in
    BILLING_TZ (`format/dates.ts`).
  - Every list uses `ListState`.
  - Add `data-ui` markers plus `docs/progress/scope.yaml` items, and update the screen's
    section in [docs/ui.md](ui.md).
  - Prepaid POSTs use one Idempotency-Key per submission (`screens/submissionKey.ts`).
- **Follow TMF:** a UI exposes only what the vendored specs and our API define. Keep
  server-side rules on the server and show their errors. Do not copy the rules into the UI.
- Done = the "Done when" line holds, new tests fail without the slice, and the
  pre-commit hook passes. Then update this file's status cell (the `git log` subject),
  and commit and push. Deploying is the human's call.
- If a slice is too large, split it into rows `7a`/`7b` here. Do not widen its scope.

Status: `todo` · `next` · `done: <commit subject>` · `split`

## Phase A: finish prepaid

| # | Slice | Scope (API -> UI) | Done when | Status |
|---|---|---|---|---|
| 1 | **Prepaid first-charge preview in Add product** | Internal `firstCharge` already returns `required`/`available`/`shortfall` for prepaid (P5a). `AddProduct.tsx` step 3 shows them for a prepaid account. The interval is paid in full, with no proration text. A shortfall is shown as "Top up X first" with a link to the Top-up form. Submit stays enabled: the server answers `insufficientFunds`, and the UI shows that error, not a duplicate client check. This was deferred by P6a ([frontend.md](decisions/frontend.md#p6a-account-mode-and-the-prepaid-card-2026-10-23)). | A prepaid preview shows required/available/shortfall in both timezones. The postpaid preview is unchanged (its existing tests pass unmodified). A test pins that the prepaid preview makes no extra request. | next |
| 2 | **Balance action history on the prepaid card** | TMF654 `list_history`, `get_history` -> a "Balance actions" list on the prepaid card: date, kind (top-up, adjustment, hold, transfer in/out, fee), amount, status, reason. It is paged with `getAll`, filtered by bucket/account, and a row opens a detail dialog. **Check first:** the internal `transactions` list (`app/repository/billing.py` `page_account_transactions`) unions only charges, payments and refunds. Adjustments and transfers are missing from it, so its running balance can differ from the prepaid ledger. Record in the decision whether the prepaid card hides that list, labels it "charges and payments", or the internal view gains the new kinds. The last option is a backend change: propose it first. | Every action kind from the mixed-mode seed (Pavel Orlov) appears once. The detail dialog renders. The decision about the transactions list is recorded and applied. | todo |
| 3 | **Cancel top-ups and adjustments, release holds** | TMF654 PATCH `status: cancelled` on `topupBalance`, `adjustBalance` and `reserveBalance`. The UI needs no GET-one beyond the dialog. It also uses PATCH `reason`/`description`, and DELETE of a `failed` action, which tombstones it. These are row actions in the slice-2 history: **Cancel top-up** (only while it is unspent and unheld, otherwise the server returns 409 and the UI shows its message), **Cancel adjustment**, **Release hold**, **Remove failed**. Each has a confirmation that states the effect. Transfers have no cancel (they are final). Deferred by P6b. | All four writes are pinned on both sides. A 409 shows the server's reason and keeps the dialog open. Success reloads the card and history. | todo |
| 4 | **Product actions: suspend, resume, deactivate on a date (both modes)** | TMF622 `create_order` with `modify` items (`product.status` suspended/active) and `delete` items with a `requestedStartDate`. These are product row actions on the subscriber card. Prepaid: suspend and resume take effect today only (the server's `prepaidLifecycleUnavailable` otherwise), and resuming after expiry queues a renewal, followed by the existing P6c2 order watch. Postpaid: date today or later. Pending changes are shown on the row and cancelled through `cancel_order`, the same way "Undo deactivation" works. This is [UI gap slice 2](ui-mcp-gap-plan.md), part 1. **Design approval** (date limits, what the dialog explains for each mode). | Suspend/resume/deactivate-on-date bodies are pinned for both modes. A prepaid funding suspension shows that resume does not lift it ("top up instead"). A pending change can be undone. | todo |
| 5 | **Change offering (both modes)** | TMF622 `modify` item with a new `productOffering`. Prepaid: allowed only at `paidThrough`. The UI offers that date only and explains that the change waits for a funded renewal (P3h). The pending change is shown on the row until it applies. Postpaid: any date today or later, with the price-change explanation that the first-charge preview gives. Gap slice 2, part 2. **Design approval.** | Bodies are pinned for both modes. A prepaid change on a date other than `paidThrough` cannot be sent. The pending change shows and can be cancelled. | todo |
| 6 | **Prepaid commands screen** | Internal `list_prepaid_commands` (filters `state`, `billingAccount.id`), `get_prepaid_command`, `retry_prepaid_command`. This is a global read screen under Billing (`/prepaid-commands`): kind, account link, state, due date, attempts, next retry, outcome/reason/error, and a needs-top-up block. Retry is shown only for `failedTechnical`. The subscriber card links to it with `?account=`. P6c2 retries only the command behind one order; this screen covers renewals too. | The list, its filters, the detail dialog and retry are pinned. A retry of a command in any other state is not offered, and a server 409 is shown. | todo |
| 7 | **Prepaid browser check and release evidence** | Extend `frontend/e2e/smoke.spec.ts` on the seeded stack. Open Pavel Orlov's prepaid card (ledger, available, history), make one top-up, and buy one product, following it to completion with `make worker-once` or the order watch. Open Vera Lebedeva and see needs-top-up. Update [prepaid-release.md](prepaid-release.md) "Not covered". | `make web-e2e` passes with the prepaid steps. The release evidence lists them. | todo |
| 8 | **Future-dated prepaid suspend/resume** (backend) | Today a prepaid status change dated after today is `prepaidLifecycleUnavailable` (`app/ordering/prepaid.py` `problems`). Postpaid supports dated suspension. **Data-model and design change: propose a plan first.** It likely needs an `order` command due on the date, like future starts. Update slice 4's date picker when this lands. Mixed purchase/status orders stay refused. **Human decision:** build this or keep today-only (record either way in [prepaid.md](decisions/prepaid.md)). | Either the decision "today-only stays" is recorded, or a dated suspend and resume complete on their date in tests (worker tick), with MCP and the UI updated. | todo |

Not planned (the plan says it is a separate workflow; raise it only if the human asks):
moving an account with history between postpaid and prepaid
([prepaid-plan.md](prepaid-plan.md) "Switching mode").

## Phase B: every MCP function in the UI

The measured gap on 2026-10-24, before Phase A. The UI already covers refunds and on-demand
bills ([gap slice 1](ui-mcp-gap-plan.md), done), numbers (TMF639), users, and TMF654
top-up/adjust/reserve/transfer. Operations covered by another route stay as they are:
`get_bill` (internal bill detail), `create/patch/delete_product` (= orders in the UI).

| # | Slice | Scope (API -> UI) | Done when | Status |
|---|---|---|---|---|
| 9 | **Sidebar sections and global Orders screen** | Already decided ([frontend.md](decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21)): three sidebar sections (Customers / Products / Billing); Financial accounts moves to Billing and loses its count; a read-only `/orders` screen with `list_orders`, `get_order`, `list_cancellations`, `get_cancellation`, state and account filters, and an order dialog. Put the slice-6 commands screen under Billing too. | As listed in the decision (sidebar test, request pins, e2e opens `/orders`). | todo |
| 10 | **Order writes: cancel and drafts** | `cancel_order` on any cancellable order from the order dialog; drafts with `patch_order` (edit a draft's items and dates), submitting a draft, and `delete_order` (draft only). **Design approval: this supersedes the "no writes in this screen" line of the 2026-10-21 decision.** Get approval and record the change there. | Cancel, draft edit, submit and delete are pinned. A non-draft cannot be edited or deleted in the UI, and the server's 409 is shown. | todo |
| 11 | **Product specifications** | `list/create/get/patch/delete_specification`. Create and edit from the Catalog screen (a "Specifications" tab, plus "New specification" inside New offering), so a fresh system no longer needs MCP to create an offering. Delete is only offered for unused ones, and the server decides. [Gap slice 4](ui-mcp-gap-plan.md). | A spec can be created and then used by New offering in one session. All writes are pinned. | todo |
| 12 | **Offering and price detail, edit, delete** | `get_offering`, `patch_offering` (beyond `lifecycleStatus`: name, description, sellable, validity, and whatever else the API lets a client change), `delete_offering`; `get_price`, `patch_price`, `delete_price`. These are an offering detail dialog and price rows in Catalog. The internal `price_change` stays the way to change a live price. The UI explains the difference and does not offer a PATCH the server refuses. | Bodies are pinned. A refused edit or delete (in use, live price) shows the server's reason. | todo |
| 13 | **Categories and product catalogs** | `list/create/get/patch/delete_category` and `..._catalog`. Catalog screen tabs, plus assigning offerings and categories where the API supports it. | CRUD for both is pinned. Offerings show their categories. | todo |
| 14 | **Catalog import and export jobs** | `list/create/get/delete_export_job`, `..._import_job`. A Catalog "Import / export" tab: start an export, see its state and download the result if the API returns one, start an import from a pasted or uploaded body, poll job state (`usePolled`), and delete jobs. Check [catalog.md](decisions/catalog.md) for what the jobs carry. | Both job types are pinned. Polling stops at a terminal state. | todo |
| 15 | **Parties and customers** | `list_individuals`, `list_organizations`, `list_customers`, their GETs, and `delete_individual/organization/customer`. A "Parties" screen under Customers, with tabs for individuals, organizations and customers, search, a link to the subscriber card when an account exists, and delete only where the server allows it (no account and no history). This makes parties without an account visible. | The lists and delete are pinned. A party without an account is reachable. A refused delete shows the reason. | todo |
| 16 | **Billing account delete, financial accounts** | `delete_account` (only accounts without history; prepaid ones cannot be deleted, so close them instead) as a card action; `create/patch/delete_financial_account` on the existing Financial accounts screen. Check [accounts.md](decisions/accounts.md#financial-accounts-slice-1) for what billing ignores. | Writes are pinned. Delete is not offered when the server would refuse it for the account's known state, and the server's 409 is shown otherwise. | todo |
| 17 | **Party and settlement accounts (TMF666)** | `list/create/get/patch/delete_party_account` and `..._settlement_account`. An "Accounts" area under Billing, with a tab next to Financial accounts. | CRUD for both is pinned. | todo |
| 18 | **Billing cycle specifications, bill formats, presentation media** | `..._cycle_specification`, `..._bill_format`, `..._bill_media` (5 operations each). A "Billing settings" screen under Billing. Selecting them on a billing account, where TMF666 allows it, is in scope if the API accepts it today. | CRUD for the three is pinned. | todo |
| 19 | **Payments and refunds history** | `list_payments`, `get_payment` (TMF676). The card's transactions show internal rows today. Add a "Payments" list on the card and a global Payments screen under Billing (payments and refunds, filters by account and date). Refunds reuse `list_refunds`/`get_refund`. | Both lists and the detail dialogs are pinned. | todo |
| 20 | **Global Bills screen, bill cycles, rates** | `list_bills` (global, filters by state, account and date), `list_bill_cycles`/`get_bill_cycle`, `list_rates`/`get_rate` (TMF678). A Bills screen under Billing (planned in the 2026-10-21 decision as "later"). It opens the existing bill dialog and links to the cycle and its rates. | The list, filters and the cycle/rate views are pinned. | todo |
| 21 | **Product inventory screen** | `list_products` across all states (the UI asks only for `status=active` today), `get_product`. A read-only Products screen under Products with filters (state, offering, account), linking to the card and the order. Writes stay orders (CLAUDE.md: orders alone create or end products). | The list and filters are pinned. Terminated and cancelled products are reachable. | todo |
| 22 | **TMF654 reads: buckets and accumulated balances** | `list/get_bucket`, `list/get_accumulated_balance`, plus `list/get` of each action kind (`list_topup_balances` ...) as a global "Balances" screen under Billing (all prepaid accounts: available, reserved, paid-through, needs top-up), linking to the cards. | The list is pinned. Postpaid accounts do not appear. | todo |
| 23 | **Event subscriptions (hubs) and system** | The 17 `create/get/delete_hub_*` tools in an admin-only "Integrations" screen: register a callback for an API, list what the UI registered, and delete. **Design approval:** whether a list exists (most hubs have no GET list), and who may use the screen (admin role only). Also a "System" panel: `billing_status`, `healthz`, and a link to `extension_schema`. | Hub create/delete are pinned. Non-admins do not see the screen. | todo |
| 24 | **Parity check** | Add a test (backend or script, `make docs-check` style) that compares the MCP operation list with the operations the UI calls, using an allowlist for documented exceptions (for example `create_product` = orders). Update [ui-mcp-gap-plan.md](ui-mcp-gap-plan.md) to "closed", then do the subscriber-card visual review from the backlog. | The check fails when a new MCP operation has no UI path and no allowlist entry. The gap plan is closed. | todo |

## Estimate

| Date | Remaining | Note |
|---|---|---|
| 2026-10-24 | 24 rows, expect 28-32 commits | Past UI slices split about 1.2× (P6c → 2). Likely splits: 4 (prepaid vs postpaid), 10, 15, 23. Slice 8 is the only expected backend/migration slice, and it may be dropped by decision. |
