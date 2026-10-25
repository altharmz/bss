# Frontend (admin UI, stage 5): decisions and conventions

## Next slices 1-24: designed (2026-10-24)

The human delegated every design decision on 2026-10-24. The UI for the remaining
prepaid actions and for MCP parity is designed in
[next-slices-design.md](../next-slices-design.md), and its progress is tracked in
[next-slices.md](../next-slices.md). Slice 10 supersedes "No writes in this screen"
below. Each slice adds its own entry here when built.

## Slice 1: prepaid first-charge preview in Add product (2026-10-24)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 1); frontend only,
no new request (the internal `firstCharge` already carried `required`/`available`/`shortfall`).
`Preview` in `AddProduct.tsx` branches on a prepaid account **and** `required` being present;
the postpaid branch is unchanged. The prepaid branch shows period, price and "paid in full in
advance"; To pay now (or To pay on the start date), Available, Shortfall (> 0 only) and Ledger
after (start today only). A shortfall with a start today shows a warning and **Top up {n}**,
which closes Add product and opens the card's Top-up form with the amount typed in
(`PrepaidMoneyModal` gains `initialAmount`; `SubscriberCard` holds it). A future start shows
the hint only. Markers `ui.addProduct.prepaidPreview`, `ui.addProduct.topUpShortfall`.
Tests (`SubscriberCard.test.tsx`, both timezones): figures today and future, the handoff with
the amount prefilled and the top-up body pinned (the POST itself is already replayed in
`tests/test_ui_requests.py`), no shortfall when covered, one preview request.
Deviation: the first product of a new subscriber (`SubscriberForms.tsx`) has no card behind
it, so there the warning shows without the Top-up button (`onTopUp` is optional).

## Slice 2: Balance actions panel (2026-10-24)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 2; the backend
half is in [prepaid.md](prepaid.md#slice-2-transactions-include-balance-actions-2026-10-24)).
`PrepaidHistory.tsx` and `api/history.ts`: the **Balance actions** panel
(`ui.subscriber.prepaid.history`, prepaid cards only, above Charge history) merges
`balanceActionHistory?partyAccount.id=` and `?receiverBucket.id=` (both `getAll`), de-duplicates
by id and sorts by `requestedDate` descending; a row opens **Balance action**
(`ui.subscriber.prepaid.historyDetail`), fetched fresh. The panel reloads with the card's
`version`; a postpaid card makes no TMF654 request. `TYPE_LABEL` gained the four new types.
Tests: `SubscriberCard.test.tsx` (request pair, order, signs and labels, detail fetch and links,
payment method and expiry, empty, error, reload after a top-up, postpaid, transaction labels) and
`tests/test_ui_requests.py::test_balance_action_history_reads_as_the_ui_makes_them` (the reads replayed).
Deviations (the code differs from the design's facts):
- A transfer body names only `receiverBucket`, not the receiving account, so the receiver
  ("Transfer to {name}", the dialog's To link) is resolved with one `GET bucket/{id}` per distinct
  receiver bucket of the sent transfers (a failure falls back to "another account").
- The detail shows the product by name or short id without a link: there is no product screen.
- A transfer or other action still `created` reads "Pending" (the design names Held only for holds).

## Slice 9: Sidebar sections and the global Orders screen (2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 9 and the 2026-10-21
decision above), frontend only. One `<nav aria-label="Main">` holds the three sections (Customers,
Products, Billing, in the design's item order; Financial accounts has no count and no count
request). `/orders` is `screens/Orders.tsx` with `api/orders.ts`; the order dialog links an
`inProgress` order to `/prepaid-commands?account=` when the journal (asked by
`billingAccount.id`) holds a command with that order link; the card links `Orders`
(`ui.subscriber.orders`). Tests: `Orders.test.tsx`, the sidebar and route tests in `App.test.tsx`,
the card link test, and `tests/test_ui_requests.py::test_orders_screen_reads_as_the_ui_makes_them`.
Deviations:
- The nav's accessible name is "Main" (was "Operations"): App, FinancialAccounts tests and the e2e
  smoke use it (TEST-CHANGE).
- `stubFetch` stubs the Orders count with none by default (like the commands badge).
- The list is oldest first, as the server orders it (no sort parameter); the caption says so.
- The state filter offers the states this service reaches, not every TMF622 state.
- The card's Orders link sits in the Billing account panel header, not beside the Bills heading.
- The e2e smoke does not open `/orders` (not run in this slice); the browser check is left for a
  later e2e pass.

## Slice 6: Prepaid commands screen (2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 6), frontend only.
Route `/prepaid-commands`, `screens/PrepaidCommands.tsx` (`ui.prepaidCommands`), sidebar item
after Billing runs with a red `failed` badge from `?state=failedTechnical&limit=1` and
`X-Total-Count` (`countFailedCommands`; refreshed after a Retry). List: pages of 50 as served,
state filter, `?account=<id>` chip (`ui.prepaidCommands.accountChip`, removing it rewrites the URL).
The row opens `ui.prepaidCommands.detail` (every field, `needs_topup` block, Retry
`ui.prepaidCommands.retry` only for `failedTechnical`, 409 shown). The prepaid KPI area of the card
links "Commands" (`ui.subscriber.prepaid.commands`). Tests: `PrepaidCommands.test.tsx` and the
replay in `tests/test_ui_requests.py::test_prepaid_order_and_command_reads_as_the_ui_makes_them`
(list query, badge, Retry POST).
Deviations:
- The API filter is `billingAccount.id` (the design's `account` is only the screen's URL parameter).
- The chip shows the account name from a loaded row, else `BA-xxxxxxxx` (no extra account request).
- Order and product links go to the card (no product screen; the order dialog came with slice 9, the links were not moved).
- `stubFetch` now stubs the badge request with no failures by default, so existing tests did not
  change; App.test's sidebar link list gained `/prepaid-commands` (TEST-CHANGE).

## Slice 5: Change offering (2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 5), frontend only.
**Change offering…** (`ui.subscriber.changeOffering`) joins the Actions menu for an active or
suspended product with no end and no pending offering change (prepaid also needs `paidThrough`).
`ChangeOfferingDialog` (`screens/ProductActions.tsx`, `orderOfferingChange` in `api/bss.ts`,
`ui.subscriber.offeringChange`) lists the Add product offerings (`loadSellableOfferings`, with a
price in the account currency, the current one excluded), takes the date and shows the first-charge
preview (`Preview` of `AddProduct.tsx`, now exported) labelled "New offering from {D}" or, prepaid,
"Renewal on {D}". Postpaid D defaults to `nextChargeDate` and may be any day from today; prepaid D
is read-only `max(paidThrough, today)`. Body: one `modify` item, `product: {id}` and
`productOffering: {id, href, @type}`, never a `status`. The pending change then shows through
slice 4's line and Undo. Tests: `SubscriberCard.test.tsx` ("change offering": menu rules, both
bodies, offering list, fixed prepaid date, rejection and 422 shown) and the replays
`tests/test_ui_requests.py::test_postpaid_offering_change_as_the_ui_sends_it` and
`::test_prepaid_offering_change_as_the_ui_sends_it`.
Deviations:
- The menu tests of slice 4 listed the items exactly, so the new item changed their expectations
  (TEST-CHANGE in the commit); no check was weakened.
- An offering without a recurring price in the account currency is not offered (the design's
  "source of Add product" lists those with a dash; a change to one has no price to preview).

## Slice 4: Product actions and pending changes (2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 4). The product row
gets **Actions ▾** (`ui.subscriber.productActions`) with Suspend, Resume and End on a date
(`ui.subscriber.suspend|resume|endOnDate`), all through `ProductChangeDialog`
(`screens/ProductActions.tsx`, `orderProductChange` in `api/bss.ts`): one TMF622 order, a `modify`
item with `product.status` or a `delete` item, with `requestedStartDate`. Pending changes show as
lines with **Undo** (`ui.subscriber.undoChange`, `cancelProductOrder` with the order ref). A
funding-suspended prepaid product shows "Waiting for funds" and a **Top up** button that opens
the Top-up form with the shortfall. Backend: `pendingChanges` on the internal account products
(see [prepaid.md](prepaid.md)), no migration. Tests: `SubscriberCard.test.tsx` ("product actions":
menu items per status and cause, every body, the rejected reasons, pending lines, Undo and its
409), `tests/test_internal_pending_changes.py`, and the replays
`tests/test_ui_requests.py::test_postpaid_product_actions_as_the_ui_sends_them` and
`::test_prepaid_product_actions_as_the_ui_sends_them`.
Deviations:
- `requestedStartDate` is noon of the date in BILLING_TZ (`requestedDate`, as the add order
  sends it), not midnight: the code, not the design's wording, is the pattern.
- **Deactivate stays a button** next to the menu instead of moving into it, because the existing
  tests click it directly (never weakened); the menu holds the new items. The Actions column is
  wider (300 px).
- The status and end orders carry no Idempotency-Key, like `orderDelete` (a repeat after an
  uncertain submission is refused by the server as a second change on the same day); an uncertain
  outcome says "Unsure whether it was saved: Refresh to check" with Refresh.
- Change offering followed in slice 5 (below).

## Slice 3: Cancel, release, remove and edit-note writes (2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 3), frontend only.
The Balance action dialog (`PrepaidHistory.tsx` `ActionWrites`, `api/history.ts`) offers Cancel
top-up, Cancel adjustment, Release hold, Remove and Edit note (markers
`ui.subscriber.prepaid.cancelTopup|cancelAdjustment|releaseHold|removeFailed|editNote`), each
cancel/remove behind an inline Yes/No with the design's texts, one request, a toast and a card
reload. A 409 shows the server's message in the dialog, which stays open; a 5xx says "Unsure
whether it was saved" with Refresh. Tests: `SubscriberCard.test.tsx` ("writes on a balance
action": every body, buttons per kind and status, the 409, the 5xx) and
`tests/test_ui_requests.py::test_balance_action_writes_as_the_ui_sends_them` (replay: reversed
top-up, released hold, 409 for a held top-up, DELETE of a refused action).
Deviations (the code differs from the design's facts):
- Every `*_Update` schema requires `status`, so Edit note sends the current status with the
  changed fields (a repeat is a no-op 200). A hold's also requires `reason`: Release hold sends
  the stored reason, or "Released by operator" when it has none, and Edit note on a hold sends
  the reason and cannot clear it.
- The cancel/remove/edit reload bumps the card `version`, and the card unmounts while it
  reloads, so the dialog closes after a successful write (the toast confirms) instead of
  showing the reloaded detail.

## Sidebar grouped by TMF area; global Orders screen (2026-10-21)

Decided by the human on 2026-10-21; built in slice 9 (entry below). It replaces the single "Operations"
heading from the design handoff (`design-ref/`, which is left unedited). TMF does not
define a UI menu. The groups follow the TMF Open API and ODA component areas
instead: party/customer, product (catalog, ordering, inventory, resources) and billing
(bills, payments, accounts).

**Sidebar** (`frontend/src/app/Shell.tsx`), three labelled sections in this order. The
section label has the same style as the old "Operations" label. Each section is its own `<nav>`, or one
`<nav aria-label="Main">` holds all three:

| Section | Items (count source) |
|---|---|
| Customers | Subscribers (billingAccount total) |
| Products | Product catalog (productOffering total), Orders (productOrder total), Numbers (no count, unchanged) |
| Billing | Billing runs (run count / red **failed** badge, unchanged), Financial accounts (**no count**) |

- **Financial accounts moves to Billing and loses its count.** It is a read-only TMF666
  aggregate that billing ignores ([accounts.md](accounts.md#financial-accounts-slice-1)).
  It runs nearly one per billing account, so its total repeated the Subscribers number.
  The sidebar's `financialAccount?limit=1` request goes away. The screen, its route and the
  card's link to it stay as they are.
- **Orders** is a new item (below). Its count uses `productOrder?limit=1&fields=id`
  and `X-Total-Count`, like the other lists. A loading or failed count stays blank.
- A later global **Bills** list belongs under Billing. It is not part of this change.
- Tests: the sidebar test asserts the three section labels, the item order per section,
  the Orders count, and that Financial accounts has no count and makes no count request.
  `docs/ui.md` Shell section and `scope.yaml` items are updated with the code.

**Orders screen** (`/orders`, `screens/Orders.tsx`, `data-ui="ui.orders"`). This replaces
[UI gap slice 3](../ui-mcp-gap-plan.md)'s per-account order history. It is read-only
and frontend-only: TMF622 `GET /productOrder` already filters by `state` and
`billingAccount.id`, and `GET /cancelProductOrder` by `productOrder.id`.

- List, paginated with the existing ListState patterns. Columns: order display id
  (first 8 hex digits, upper case, as elsewhere), subscriber (account name linking to the
  subscriber card), state pill, item actions (for example "add Basic 10", "delete ..."),
  requested start date, creation date (business time zone), completion or cancellation
  date. Filters: state (TMF622 states), and account through `?account=<billingAccount id>`
  in the URL, shown as a removable chip with the account name. Reset filters as in other
  lists.
- The subscriber card links to `/orders?account=<id>` ("Orders" next to the Bills/
  transactions headings). The card itself shows no order list.
- Row opens an **order dialog**: header fields, each `productOrderItem` (action,
  offering, product link, item state, item price), `productOrderErrorMessage` when present,
  and the order's cancellations (`cancelProductOrder?productOrder.id=`), with date,
  state and reason.
- **No writes in this screen.** Drafts (create, PATCH, submit, DELETE) and cancelling
  stay in the API/MCP. "Undo deactivation" stays on the card. Modify orders (suspend,
  resume, change offering) remain gap slice 2.
- Tests: component tests pin the list request (filters, paging), the account chip from
  the URL, the dialog's item and cancellation rendering, and the error and empty states.
  `tests/test_ui_requests.py` replays the GET filters against the backend. The e2e opens
  `/orders` and one dialog read-only.

## P6a: account mode and the prepaid card (2026-10-23)

No backend change. `Account.ratingType` comes from the TMF666 body (a missing one is an
error like any other attribute).

- **Creation.** New subscriber has an Account mode switch (Postpaid default, Prepaid).
  `ratingType: "prepaid"` is sent only for prepaid, so a postpaid body is byte-for-byte what
  it was; a prepaid account hides the credit limit field and sends none (a positive limit
  is a 422). Edit does not change the mode (the backend allows only postpaid to prepaid
  before history, never back).
- **List.** An Account mode filter (All / Postpaid / Prepaid, with counts) next to the state
  filter; a prepaid row says "· prepaid" in the name cell. No new column: a dedicated one
  would have changed every existing row assertion for little gain.
- **Card.** For a prepaid account the KPI strip drops Credit limit, Available incl. credit
  and "In arrears", and shows five figures that are never merged: **Ledger** (TMF666
  `currentBalance`, may go below zero), **Available** and **Reserved** (TMF654 bucket
  `remainingValue` = ledger - holds, `reservedValue`; read by `partyAccount.id`; the ledger is
  not recomputed from them), **Paid through** (earliest `paidThrough` of the open products,
  from the internal product view) and **Shortfall** (largest `needs_topup.shortfall` among
  products, with required/available, the product count and the distinct `suspensionCause`s
  in words). A failing bucket shows an alert in the strip and the rest of the card works.
  A postpaid card makes no TMF654 request. The bucket types are hand-written in
  `api/bss.ts` (TMF654 is a Swagger 2 file the type generator does not cover).
- **Not here.** Money forms (P6b), transfer, pending polling and technical retry (P6c), and
  the prepaid preview inside Add product (the internal `firstCharge` already carries
  required/available/shortfall) are later slices.

## P6b: prepaid money forms (2026-10-24)

No backend change. A prepaid card header offers **Top up**, **Adjust** and **Hold funds**
(disabled until the bucket has loaded; the bucket's `id` is now read with the figures).
`PrepaidMoney.tsx` is one modal for the three; the requests are built in one place
(`balanceActionRequest` in `api/bss.ts`) and pinned in `SubscriberCard.test.tsx` (both time
zones) and replayed against the real API in `tests/test_ui_requests.py`.

- **Bodies.** Top-up: `TopupBalance` with `partyAccount`, `paymentMethod {id, name}` (TMF654's
  `PaymentMethodRef` requires an `id`; the method name is both) and an optional `reason`.
  Adjust: `AdjustBalance`, no `partyAccount` (the API refuses one); a debit is the same
  amount sent negative (`jsonWithSignedAmount`), so the operator never types a sign. Hold:
  `ReserveBalance` with `partyAccount` and, if a date is given, `validFor.endDateTime` =
  midnight at the end of that business day in BILLING_TZ ("holds through" the date).
  Amounts are verbatim exact tokens, as everywhere.
- **Limits in the form.** A debit or hold above the available funds is refused before
  anything is sent (the API would store a `failed` action); a hold date before the business
  date is refused. Closed accounts are not special-cased: the API stores that refusal.
- **Idempotency-Key.** One key per submission. After an uncertain outcome (no response, a
  5xx) the button becomes Retry and the unchanged form is sent again with the **same** key,
  so the server replays what it stored instead of acting twice. The key belongs to the exact
  path + body: an edited form gets a new key (the same key with another body is a 409).
  A success, a 4xx or a stored refusal ends the submission, so the next one has a new key.
- **Stored refusal.** A business refusal is a 201 whose `status` is `failed`: the form shows
  its `reason` as an error, shows no success toast, and does not reload the card.
- **Not here.** Cancelling a top-up, adjustment or hold, and releasing a hold, are not in
  this slice (the plan lists only the three creates); transfer is P6c1, pending orders and
  technical retry P6c2.

## P6c1: transfer and its polling (2026-10-24)

P6c is split: this slice is the transfer and the polling of the accepted transfer; pending
orders and the technical retry are P6c2. No backend change. A prepaid card header gains
**Transfer** (`PrepaidTransfer.tsx`, `api/transfer.ts`).

- **By number.** The sender picks one of its own numbers (active products' resources); the
  receiver is typed and looked up on **Find**: inventory `value=`, the non-terminated product
  that uses the number, its billing account, that account's bucket. Refused before anything
  is sent: an unknown or unused number, the same account, a postpaid receiver (no bucket),
  another currency, an amount above the available funds, an empty reason (the API requires one).
- **Finality.** The form's button is Review transfer; the confirmation step names amount,
  numbers and receiver and says the transfer cannot be cancelled or edited, and only an
  opposite transfer reverses it. Nothing is sent before Confirm.
- **Body.** `TransferBalance` with `channel` `admin-ui`, `logicalResource`,
  `receiverLogicalResource`, both buckets and `receiverBucketUsageType`; no `transferCost`
  (absent means 0.00 paid by the originator, so the UI never posts a fee). Pinned in
  `SubscriberCard.test.tsx` (both time zones) and replayed in `test_ui_requests.py`.
- **Idempotency-Key.** The same discipline as P6b; its key bookkeeping now lives in
  `submissionKey.ts`, shared by both forms.
- **Refusal text.** A refused transfer is a stored 201 `failed`: its explanation is in
  `description` (`reason` is the operator's own words), so the form shows `description`.
- **Polling.** An accepted transfer is `created` until the worker's next wake-up. The screen
  (above the card, which unmounts while it reloads) follows `GET transferBalance/{id}` every
  5 s for at most 3 minutes from the submission (`usePolled`'s new `until`; the cap is a
  timer, not a clock read in render). `completed` and `failed` toast and reload the card.
  Past the cap it says the amount stays held and the worker completes it on its next wake-up,
  and offers Check now and Dismiss; a failed look stops the polling and offers Check again.
  Transfer is disabled while a transfer is followed, so Dismiss frees it after a stall. The
  followed work lives in `SubscriberCardScreen` as a list (`watched.ts`), and the route keys
  the screen by account so one subscriber's watch never shows on another's card.

## P6c2: pending prepaid orders and technical retry (2026-10-24)

No backend change. A prepaid account answers an order POST with `inProgress` (the billing
worker completes it), but Add product, Deactivate and the number change treated anything but
`completed` as a failure, so every successful prepaid order was shown as an error. They now
treat `inProgress` as accepted: the dialog closes, a toast says so, and `OrderWatch` follows
the order through its command (a prepaid order's id is its command's id, `GET
/internal/v1/prepaid/command/{id}`), polled like the transfer (5 s, 3 min cap, Dismiss).
The three outcomes stay apart:

- **Waiting** for the worker: polled. A command **scheduled** for a later date than the
  business date says so and is not polled (a future-start order waits for its start).
- **Needs top-up** (`needs_topup`): a funding wait, not a fault. It shows required, available
  and short, says it completes once the account is funded, stops polling and offers Check now.
  Renewals' shortfall stays in the shortfall KPI of the card.
- **Technical failure** (`failedTechnical`): shows `error` and when the worker retries by
  itself, with **Retry now** (`POST .../retry`, no body, 202). A 409 (already retried) just
  looks again. A **Needs attention** panel lists the account's technically failed commands
  (`?billingAccount.id=&state=failedTechnical`) of any kind, with the same Retry; it appears
  only while there are some.

A completed command settles the watch with the outcome (`funded` is "completed", `terminated`
"ended"). Pinned in `SubscriberCard.test.tsx` and replayed in `test_ui_requests.py` (an order's
id is its command's id; the list query; the retry and its 409).

## N5: subscriber-number UI (2026-10-21)

`/numbers` lists and searches inventory, creates strictly formatted E.164 digits,
shows allocation/usage/administrative state and linked products, and locks/unlocks
through TMF639. Lock copy explains that accepted claims and service continue.
TMF639 types are generated from the pinned vendored contract.

Add product resolves the selected offering's specification before enabling submission.
An `msisdn` requirement reveals an explicit number picker; the order validates the
complete interval. The picker shows currently available unlocked inventory; future
releases still require a later refresh. New subscriber offers an optional initial
product step after account creation; closing it opens the already-created account.
There is no rollback or repeated account creation after this step.

The card's Subscriber number dialog reads the public product projection and permits
number changes for requiring products through a modify order effective today. Price
and termination behavior remain in the existing order workflows. Failed uncertain
writes disable resubmission and direct the operator to inspect inventory/product
state. Component tests pin inventory/add/modify requests; backend tests replay them.
Browser smoke opens inventory and validates the creation form without creating data.
N6 owns release validation; deployment follows the normal cycle.

The refund/bill-delivery milestone adds the ledger type `payout`, displayed as
“Refund paid out”. The existing `refund` label still denotes an ABR credit.
Refund and presentation management remained API/MCP workflows in that milestone.
The approved UI follow-up below adds refund recording and on-demand bill requests.

## Refunds and on-demand bills UI (2026-10-20)

The human approved slice 1 of [the UI gap plan](../ui-mcp-gap-plan.md#slice-1-design-for-approval-2026-10-20).
Refund unused credit lives beside the card's unused-credit figure, limited by both
unused credit and the positive account balance. It records an already completed
payout with amount, method and optional description; it never sends money. Closed
and Suspended accounts remain eligible. Sources are selected by the API, without a
payment restriction. Refund metadata has its own list, alongside the existing
“Refund paid out” ledger entries.

Bill now in the Bills heading queues the existing TMF678 request. The confirmation
explains processing after a successful account run from tomorrow onward. A request
list shows progress, server explanations and a link opening the existing bill dialog.
One pending request disables another submission. Polling is bounded to three minutes,
with explicit refresh afterward. A credit/request conflict refreshes availability or
status; uncertain writes disable resubmission in the dialog and ask the operator to
check history/status. No write is retried automatically. No backend billing or data
model changes are part of this slice.

Index: [README.md](README.md). Screen-level detail:
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
- Recurrence offers daily, weekly, monthly and yearly with a positive multiplier
  (1..1000), since 2026-10-20; see [charge-periods.md](charge-periods.md). One currency
  in the UI (`UI_CURRENCY`); the backend stays multi-currency.
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
  depend on cloud mode, see ["Agent guardrails"](harness.md#the-guard)); `guard.sh` blocks a command that runs a Node
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
