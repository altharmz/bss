# UI vs MCP: operation gap and plan (2026-10-20)

**Status: slice 1 implemented and validated locally; not committed or deployed.**
The human approved the design below on 2026-10-20. Other slices still need design approval before
implementation (workflow in [CLAUDE.md](../CLAUDE.md#working-style)).

## How this was measured

- MCP: every operation of `app.openapi()` minus `EXCLUDED` and `AUTH_OPERATIONS`
  (`bss_mcp/tools.py` `api_operations`, `bss_mcp/operations.py`) - **145 tools**; the
  tool name is the operationId.
- UI: every request made in `frontend/src` outside tests (`src/api/bss.ts`,
  `src/app/App.tsx`, `src/app/Shell.tsx`, `src/app/session.tsx`, `src/screens/Users.tsx`).
- Result: the UI calls **40** of the 145 operations. It also calls the 7 session/user
  operations MCP leaves out on purpose (`AUTH_OPERATIONS`).

Re-measure before starting a slice: the lists above change with the code.

## What the UI calls today

| Area | Operations |
|---|---|
| TMF632/629/666 subscriber | individual/organization POST, GET one, PATCH; customer POST, GET one, PATCH; billingAccount list, GET one, POST, PATCH |
| TMF666 financial accounts | list (read-only screen, sidebar count) |
| TMF620 catalog | productOffering list, POST, PATCH (`lifecycleStatus` only); productOfferingPrice list, POST; productSpecification list |
| TMF622 ordering | productOrder POST with `add` and `delete` items only; cancelProductOrder POST ("Undo deactivation") |
| TMF637 inventory | product list (`status=active`), GET one |
| TMF678 bills | customerBill list per account, PATCH `state` |
| TMF676 payments | payment POST |
| Internal | context, firstCharge, products, settlement, charges, transactions, priceChange, customerBill detail, billingRun list/get/accounts/catchUp/retry |

## What only MCP reaches

### 1. Business actions with no UI path

| Capability | Operations |
|---|---|
| Refunds | `create_refund`, `list_refunds`, `get_refund` |
| On-demand bill | `create_bill_demand`, `list_bill_demands`, `get_bill_demand` |
| Change offering, suspend, resume | `create_order` with `modify` items (the UI sends only `add`/`delete`); [modify items](decisions/tmf-api.md#modify-items-2026-10-18), [suspension](decisions/tmf-api.md#suspension-2026-10-19) |
| Order history and drafts | `list_orders`, `get_order`, `patch_order`, `delete_order`, `list_cancellations`, `get_cancellation` |
| Payment history (TMF676) | `list_payments`, `get_payment` (the UI shows internal transactions instead) |

### 2. Catalog administration

- productSpecification create/get/patch/delete. **The UI lists specifications when creating
  an offering but cannot create one.** A fresh system needs MCP or the API first.
- productOfferingPrice get/patch/delete; productOffering get/delete.
- category (5 operations), productCatalog (5), importJob/exportJob (8).

### 3. Account-side configuration (TMF666)

- financialAccount create/patch/delete.
- partyAccount (5), settlementAccount (5), billingCycleSpecification (5), billFormat (5),
  billPresentationMedia (5).
- billingAccount DELETE.

### 4. Party and customer housekeeping

- individual/organization/customer lists. The UI reaches parties only through billing
  accounts, so a party or customer without an account is invisible.
- individual/organization/customer DELETE.

### 5. Covered differently, not a gap

- `get_bill`: the UI uses the internal `bill_detail`.
- `list_rates`/`get_rate`: the UI uses the internal `charges`.
- `list_bill_cycles`/`get_bill_cycle`: no UI use.
- `create_product`/`patch_product`/`delete_product`: the UI uses orders directly, with the
  same effect.

### 6. Probably not UI material

- The 17 hub tools (create/get/delete across eight APIs): integration plumbing.
- `extension_schema`, `billing_status`, `healthz`.

## Plan: slices in priority order

Every slice is frontend-only unless its own design finds otherwise (the API operations
exist and are tested). Per slice: a design note for the human, pinned write requests on
both sides ([frontend conventions](decisions/frontend.md#frontend-conventions)),
`data-ui` markers plus `scope.yaml` items, component and request tests (the pre-commit
hook runs web-lint/web-test in both timezones); `make web-e2e`, `make progress` and
negative proofs only when judged useful. Commit, push and deploy need no approval.

1. **Refunds and on-demand bill on the subscriber card.** "Refund unused credit" (TMF676
   refund POST, list on the card) and "Bill now" (customerBillOnDemand POST, with its state
   shown until the next account run produces the bill). Open: which refund inputs the
   dialog offers (amount, payment method), and whether refunds go into the existing
   transactions list or into their own list.
2. **Change offering, suspend, resume on a product.** Product row actions placing TMF622
   orders with `modify` items and a `requestedStartDate`. Show pending changes and allow
   cancellation through cancelProductOrder, as "Undo deactivation" does. Open: the date
   picker's limits (today or later, as the API enforces) and the proration explanation
   shown before submitting.
3. **Orders screen** (redesigned and approved 2026-10-21). This is a global read-only `/orders` list
   filtered by state and account, linked from the subscriber card, with an order dialog
   showing items and cancellations. Drafts stay API/MCP. Design:
   [frontend.md](decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21).
4. **Product specifications in the UI.** Create and edit specifications from the offering
   screen, so creating an offering no longer depends on MCP or the API.
5. **Optional, only on request:** payment history (TMF676 list) on the card; party and
   customer lists with deletes; price edit and delete; categories and catalogs; TMF666
   account configuration (financial/party/settlement accounts, cycle specifications,
   formats, media); import/export. Hubs stay MCP and API only.

Slices 1-2 add user-visible billing actions; check them on the subscriber card in the
same visual review as the pending one in the CLAUDE.md backlog (item 6).

## Slice 1 design for approval (2026-10-20)

Frontend-only: use the existing refund and on-demand APIs, with no schema, worker,
or billing changes. The human approved this design on 2026-10-20.

### Record a refund

Place **Refund unused credit** beside the Unused credit figure on the subscriber
card, keeping the header actions compact. Enable it after settlement loads when
the lesser of unused credit and positive account balance is greater than zero.
Suspended and Closed accounts remain eligible. Show the calculated amount as
“Refundable credit”; the server rechecks availability when saving.

The dialog is titled **Record refund**. It offers an amount (initially the displayed
refundable credit, editable), method (bankTransfer, card, cash, Other with a required
name, matching Record payment), and optional description. Currency is fixed to the
account. Accept positive amounts with at most two decimals, up to the displayed
limit, using the existing decimal-string and integer-cent helpers.

Show: “Record a refund you have already paid out. This does not send money.” The
submit button says **Record refund** and is disabled while saving. POST to TMF676
`/refund` with `account`, `totalAmount`, `paymentMethod`, and an optional nonblank
`description`. Do not send a payment reference: the API selects available payment
and credit-note sources. Payment-specific refunds and tax-entry controls stay out
of this slice.

On success close the dialog, show a confirmation, and reload the account,
settlement, bills, and history. The existing transaction history shows **Refund
paid out**. Add a separate **Recorded refunds** list on the card, loaded with
`account.id`, showing date, amount, method and description from the refund API;
this provides the payout metadata without duplicating ledger calculations.
Errors stay in the dialog with the entered values intact. A credit conflict also
refreshes the account and settlement so the operator can review current availability.
Never automatically retry a POST after an uncertain network result; advise checking
Recorded refunds before submitting again.

### Request a bill

Place **Bill now** in the Bills panel heading. It opens a confirmation dialog with:
“Request a bill for unbilled charges. The request is processed after a successful
account run from tomorrow onward. If no unbilled charges remain, it is rejected.”
The submit button says **Request bill**. There are no amount or date inputs. POST
to TMF678 `/customerBillOnDemand` with only `billingAccount` (plus required TMF
reference/type metadata following existing request conventions).

Show a **Bill requests** list below the bills, filtered by `billingAccount.id`:
last update in the business timezone, state, server explanation when present, and
an **Open bill** action when `customerBill` exists, reusing the existing bill dialog.
Use the API states `inProgress`, `done`, `rejected`, and `failed` with readable labels.
Disable Bill now while requests are loading, on a list error, during submission,
or while an `inProgress` request exists. Do not restrict by account state: the API
accepts requests for suspended and closed accounts too.

Refresh requests every five seconds while one is pending and the card is mounted,
for at most three minutes; then show that processing continues and offer Refresh.
Returning to the card reloads the status. Completion refreshes bills, account and
settlement. An existing-request conflict refreshes requests. Uncertain POST results
prompt a status refresh before another submission; no automatic write retry.

### Validation and completion

Both new lists use the existing ListState loading, error/retry and empty patterns,
and the API client's complete paginated reads. Preserve the existing session/role
rules and UI tokens; add no dependencies. Add scoped progress items and data-ui
markers for both actions and their lists, then regenerate progress.

Pin exact write bodies in component tests and `tests/test_ui_requests.py`. Cover
decimal validation, credit limits, closed accounts, duplicate-submit prevention,
API errors, uncertain writes, request conflicts, polling cleanup/timeout and
completion, rejected/failed states, and opening the resulting bill. Run frontend
lint, tests in both configured timezones, browser smoke, backend request tests and
the required full backend passes. Negative proofs must catch an excessive refund
and an incorrect bill-request account reference. Present the final diff and checks
before requesting commit approval; deployment remains a separate action.

Validation completed: three full backend passes (1,406 each, dev/CI/dev), 277 UI
tests in UTC and America/New_York, five read-only browser smokes, backend/frontend
lint and decisions check. Negative proofs removing the refund limit and corrupting
the bill request's account reference failed as expected; both were restored.
`make progress` passed its full backend and UI collection and records all four new
items as implemented/tested, deployment unknown. Its backend run had the two existing
in-process pytest warnings. `docs-check` regenerated the existing TMF620/666 cleanup,
but its comparison with Git still flags those pre-existing uncommitted generated pages.
