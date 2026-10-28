# Next slices: design (decided 2026-10-24)

Every design decision for the next-slices tracker (all slices done; the tracker was removed, see `git log`), made in advance. The human
delegated the decisions on 2026-10-24 ("make all required decisions yourself based on
TMF best practices"). They follow TMF where TMF defines something and the existing
decisions where it does not. **An implementing session builds what this file says and
asks nothing.** It stops only when the code contradicts a fact stated here (see "When
the spec is wrong").

As each slice is built, the implementing session writes a short decision entry in the
named decision file. The entry gives what was built, the tests, and any deviation from
this file with its reason. It links back here instead of copying this text.

## Rules for every slice

- **Pattern sources.** The prepaid money forms are in `screens/PrepaidMoney.tsx` (one
  key per submission, same-key retry). Polling is in `screens/PrepaidOrderWatch.tsx` and
  `api/usePolled.ts` (5 s, 3 min cap). Row actions with confirmation are `ProductRow` in
  `screens/SubscriberCard.tsx`. Lists use `ListState` and the `getAll` paging. Dialogs
  use `Modal` from `ui/kit.tsx`. Request pins are in `tests/test_ui_requests.py`.
- **Server decides.** The UI hides an action only when the data it already shows makes
  the server's refusal certain (for example Cancel on a `failed` action). Otherwise it
  offers the action and shows the server's 409/422 message in the dialog or row, keeping
  the entered values. No client copy of business rules.
- **Writes.** There is no automatic retry of a write. TMF654 POSTs and the order POST
  carry an Idempotency-Key (the existing helper). PATCH/DELETE are idempotent by nature;
  after an uncertain result the dialog says "Unsure whether it was saved: Refresh to
  check" and offers Refresh.
- **Dates** are business dates in BILLING_TZ, sent as `zonedDateTime` where an instant is
  required (as today). Date inputs default to the business date, never the browser's.
- **Markers.** Every new screen, action and list gets a `data-ui` marker
  (`ui.<screen>.<thing>`), a `docs/progress/scope.yaml` item and a line in `docs/ui.md`.
  New routes go in `app/router.tsx` and the sidebar section the slice names.
- **Backend parts** (slices 2, 4, 8, 23) change the internal contract
  `spec/internal/bss-internal.oas.yaml` first, then the code, `make docs`, and the MCP
  registry if an operation is added (`bss_mcp/operations.py` must name every operation).
- **Not in scope anywhere:** new dependencies, browser storage beyond what exists, and
  changes to the TMF specs in `spec/`.

### When the spec is wrong

If the code contradicts a stated fact (a filter does not exist, a field has another
name, a rule differs), follow the code and the TMF spec. Record the deviation in the
slice's decision entry and continue. Stop and report only if following the code would
need a migration this file does not plan, or would change billing.

---

## Phase A

### 1. Prepaid first-charge preview in Add product

Decision file: frontend.md. Frontend only.

Facts: `GET /internal/v1/billingAccount/{id}/firstCharge` on a prepaid account returns
`required`, `available` and `shortfall` (Money, absent on postpaid). It also returns
`prorated: false`, `next: null`, `amount` (the interval with its taxes) and
`balanceAfter` = ledger - interval. For a future start the funds are today's, and
nothing is held until the start date.

Decisions:

- `Preview` in `AddProduct.tsx` branches on `account.ratingType === 'prepaid'` **and**
  `required` being present. The postpaid branch is byte-for-byte unchanged.
- Prepaid layout:
  - Left column: **Period** (start - last day), **Offering price** (price /
    period label), **Charging**: "paid in full in advance, no proration".
  - Right column:
    - **To pay now** = `required`, large (`data-testid="prepaid-required"`). For a future
      start the label is **To pay on {startDate}**.
    - **Available** = `available`.
    - **Shortfall**, shown only when it is > 0, in the negative style.
    - **Ledger after**: shown only for a start today, as `balanceAfter`.
- Shortfall > 0 and start = today: a `bss-warning` says "Insufficient funds: top up at
  least {shortfall} {cur}. The order is refused until the account has the funds." It
  holds a **Top up {shortfall}** button (`data-ui="ui.addProduct.topUpShortfall"`).
  The button closes Add product and opens the card's existing Top-up form with the
  amount prefilled. `PrepaidMoneyModal` gains an optional `initialAmount` (decimal
  string). There are no nested modals.
- Shortfall > 0 and a future start: a `bss-hint` (not a warning) says "Funds are checked
  on {startDate}; nothing is held now. Top up before then." There is no button.
- Footnote for prepaid: "Submitting holds the full interval with its taxes; the billing
  worker charges it and renews at the paid-through date while funds last." A future
  start says "On {startDate} the billing worker takes the interval from the funds
  available then."
- Submit stays enabled with a shortfall. The server's stored rejection
  `insufficientFunds` shows through the existing rejected-order path.
- Markers: `ui.addProduct.prepaidPreview`, `ui.addProduct.topUpShortfall`.
- Tests:
  - Prepaid figures for a start today and a future start, in both timezones.
  - The warning and the Top-up handoff with the amount prefilled.
  - No extra request (the request count equals the postpaid case).
  - The existing postpaid preview tests unchanged.

### 2. Balance action history; transactions include balance actions

Decision files: frontend.md (UI) and prepaid.md (the transactions change).

**Backend part: a bug fix with no migration.** The ledger L (`account_balance`) adds
`BALANCE_ACTION_LEDGER`: adjustments (completed or cancelled), reversals, and completed
transfers in and out. The card's transaction history (`page_account_transactions`) does
not, so on a prepaid account its newest `balanceAfter` differs from the ledger once an
adjustment or transfer exists.

- Extend `page_account_transactions` and its count query with four arms. Each is
  scoped to the account. `day` is the instant in `tz`, `rank` is 1, and `at` is the
  instant:
  - `adjustment`: `balance_action` kind `adjustment`, status in (`completed`,
    `cancelled`), with its signed `amount`, at `confirmed_at`. The note is `reason`, else
    "Adjustment".
  - `reversal`: `balance_action_reversal`, with its signed `amount`, at `reversed_at`.
    The note is "Cancelled top-up" or "Cancelled adjustment" (join the action's kind).
  - `transferIn`: kind `transfer`, status `completed`, `receiver_billing_account_id` =
    account, `+amount`, at `confirmed_at`. The note is "From {sender account name}".
  - `transferOut`: kind `transfer`, status `completed`, `billing_account_id` = account,
    `-amount`, at `confirmed_at`. The note is "To {receiver account name}".
  - Fees are already ABRs (charge "Transfer fee"). A top-up's entry is its payment
    (already there). Holds never change L and stay out.
  - Guard the arms with `has_balance_ledger(conn)` as `account_balance` does.
- Internal contract: `Transaction.type` enum adds `adjustment`, `reversal`, `transferIn`,
  `transferOut`. Update the `note` description. `make docs`. The MCP `transactions` tool
  follows automatically.
- Test (`tests/test_internal_transactions_prepaid.py` or beside the existing
  transactions tests):
  - Setup: a prepaid account with a top-up, a credit and a debit adjustment, a cancelled
    adjustment, a transfer out with an originator fee, and a transfer in.
  - Each entry appears once, the total count is right, and the newest `balanceAfter`
    equals `account_balance`.
  - A postpaid account's list is unchanged.

**Frontend part.**

- `TYPE_LABEL` adds Adjustment, Reversal, Transfer in, Transfer out.
- New panel **Balance actions** (`data-ui="ui.subscriber.prepaid.history"`, subtitle
  "TMF654 balanceActionHistory"), on prepaid cards only, placed above Charge history.
  - Loads `GET /tmf-api/prepayBalanceManagement/v4/balanceActionHistory?partyAccount.id={account}`
    and `...?receiverBucket.id={bucketId}` (transfers received), both with `getAll`. They
    are merged, de-duplicated by `id` and sorted by `requestedDate` descending. The bucket
    id is the one the card already loads.
  - Columns:
    - **Requested** (business date and time).
    - **Action**: Top-up; Credit / Debit adjustment (by the sign of `amount.amount`);
      Hold; "Transfer to {receiver}" or "Transfer from {sender}", by which side this
      account is on.
    - **Amount**: signed from this account's view. Holds are unsigned, shown in the
      secondary style.
    - **Status**: a pill. Top-up/adjustment/transfer `completed` reads "Completed"; a
      reservation `created` reads "Held", `cancelled` reads "Released" or "Cancelled"
      for the others; `failed` reads "Refused".
    - **Note**: `reason`/`description`.
- A row opens **Balance action** (`ui.subscriber.prepaid.historyDetail`).
  - It fetches `GET .../balanceActionHistory/{id}` fresh.
  - It shows id (display form), kind, status, amount, requested and confirmation dates,
    reason, description, payment method (top-up), expiry `validFor.endDateTime` (hold),
    the sender and receiver accounts with links to their cards (transfer), transfer cost
    and who pays it, and the product link when present (a hold an order owns).
- Reload the panel whenever the card's `version` bumps (after any money form, transfer
  or order).
- Tests: the merged request pair, sorting, labels for every kind, signs from the sender
  and receiver views, the detail fetch, the empty and error states, and that a postpaid
  card makes no TMF654 request.

### 3. Cancel top-ups and adjustments, release holds, remove refused actions

Decision file: frontend.md. Frontend only.

API facts: PATCH `{base}/topupBalance|adjustBalance|reserveBalance/{id}` (merge patch)
with `status: cancelled`:
- A top-up: completed, unspent and unheld, else 409.
- An adjustment: completed.
- A reservation: `created`, operator-owned (a hold an order or renewal owns is a 409).

Repeating the current status is a no-op 200. PATCH may also change `reason` and
`description`. DELETE removes only a `failed` action (204) on any of the four paths,
including `transferBalance`. Transfers cannot be cancelled.

Decisions: actions in the Balance action detail dialog from slice 2, not in the rows
(they keep the list calm):

| Shown when (from the entry) | Button | Request body | Confirmation text |
|---|---|---|---|
| top-up `completed` | **Cancel top-up** (`ui.subscriber.prepaid.cancelTopup`) | `{"status":"cancelled"}` | "Takes {amount} back off the balance. Only possible while none of it was spent or held. The payment record stays." |
| adjustment `completed` | **Cancel adjustment** (`...cancelAdjustment`) | `{"status":"cancelled"}` | "Reverses this {credit/debit} of {amount}." |
| reservation `created` | **Release hold** (`...releaseHold`) | `{"status":"cancelled"}` | "Makes {amount} available again." |
| any kind `failed` | **Remove** (`...removeFailed`) | DELETE, no body | "Deletes this refused request. It moved no money." |
| top-up, adjustment or reservation, any status but `failed` | **Edit note** (`...editNote`) | `{"reason": ..., "description": ...}` (only changed fields; an empty field sends `null`) | none |

- Each button opens an inline confirm (Yes/No, like Deactivate). It sends one request,
  then reloads the detail, the history, the balance KPIs, the transactions and the
  products (`version` bump) and shows a toast.
- A 409 shows the server's `message` in the dialog, and the dialog stays open. For an
  owned hold the server's text is enough.
- Tests: the four PATCH bodies and the DELETE are pinned in the component tests and in
  `tests/test_ui_requests.py` (replaying each against the backend: a cancelled top-up
  reverses, a released hold frees funds, a 409 for a spent top-up). Also: buttons per
  status, and the 409 shown in place.

### 4. Product actions: suspend, resume, end on a date (both modes)

Decision files: frontend.md (UI) and tmf-api.md (the internal field). **This slice
includes a small backend part with no migration.**

**Backend: `pendingChanges` on the internal account products.** P3h already named this
field as owed to a later slice. Add to `AccountProduct` in the internal contract:

```yaml
pendingChanges:
  type: array
  items:
    type: object
    additionalProperties: false
    required: [kind, date, productOrder]
    properties:
      kind: {type: string, enum: [status, offering]}
      date: {$ref: '#/components/schemas/BusinessDate'}        # the change day D
      status: {type: string, enum: [suspended, active], nullable: true}   # kind status
      productOffering: {$ref: '#/components/schemas/NullableNamed'}       # kind offering
      productOrder: {$ref: '#/components/schemas/Link'}         # the order to cancel
```

- `status` entries: `product_status_change` rows of the product with day > today (not
  yet applied). For prepaid after slice 8: the pending dated `order` commands that change
  status.
- `offering` entries: `product_offering_change` rows not yet in effect. Postpaid: day >
  today. Prepaid: `effect_announced_at IS NULL` (a change waiting for a funded renewal
  can be dated in the past).
- Order by date. Always present (`[]` when there is none). The MCP `open_products`
  follows automatically.
- Tests: postpaid future suspend and future offering change; prepaid pending change at
  `paidThrough`; an applied change disappears.

**Frontend.** The product row's actions become a small menu button **Actions ▾**
(`ui.subscriber.productActions`). It replaces the single Deactivate button. "Undo
deactivation" stays as it is. Items, depending on the product:

| Item | Offered when | Order body (TMF622 POST `productOrder`) |
|---|---|---|
| **Suspend…** (`ui.subscriber.suspend`) | status `active` and no pending status change | one `modify` item, `product: {id, status: "suspended"}`, `requestedStartDate` |
| **Resume…** (`ui.subscriber.resume`) | status `suspended` and `suspensionCause` is not `funding` or `account`, and no pending status change | the same with `status: "active"` |
| **Change offering…** | slice 5 | |
| **End on a date…** (`ui.subscriber.endOnDate`) | no end set | one `delete` item with `requestedStartDate` |
| **Deactivate** (today, existing) | no end set | unchanged |

- **The dialog** (one component, `ProductChangeDialog`): it gives the action, a date
  field and an explanation, then the submit. It sends the order body exactly as the
  existing `orderDelete` builds it (billing account ref with `ratingType`, item `@type`,
  `requestedStartDate` as `zonedDateTime` of the date at 00:00 BILLING_TZ, as the add
  order does).
  - **Postpaid date:** default today, minimum today (the browser `min` is a hint; the
    server decides). Explanation: "Billing does not change while a product is suspended:
    it is charged by its dates" (decision: suspension is billed, tmf-api.md
    Suspension). For End on a date: "The product is billed up to the day before {date}."
  - **Prepaid date:** fixed to today for Suspend/Resume until slice 8 lands. The date
    field is shown read-only with "Prepaid status changes take effect today". End on a
    date allows today or later, and the explanation says "Unused paid days from {date}
    are credited to the balance."
  - **Resume after the paid interval** (prepaid, `paidThrough` < today): the explanation
    says "The paid interval has ended: resuming starts a new interval paid from the
    balance." The response is `inProgress`, and the existing `OrderWatch` follows it.
  - Funding-suspended prepaid product: instead of Resume, the row shows the existing
    needs-top-up text and a **Top up** link that opens the Top-up form prefilled with
    `needs_topup.shortfall`.
- **Pending changes on the row:** one line per `pendingChanges` entry, for example
  "Suspends on 2026-11-03" or "Changes to Basic 20 on 2026-11-01 (waits for funds)".
  The prepaid "(waits for funds)" shows when D < today. Each line has **Undo**
  (`ui.subscriber.undoChange`), which posts `cancelProductOrder` with only the
  `productOrder` ref, the same way `undoDeactivation` does. A 409 shows in the row.
- Outcomes: `completed` gives a toast and a reload. `inProgress` is followed by
  OrderWatch. `rejected` shows the reasons in the dialog, which stays open.
- Tests:
  - Pinned bodies for suspend, resume and end-on-date for postpaid (future date) and
    prepaid (today), in the component tests and `test_ui_requests.py` (replayed:
    completed or inProgress as expected).
  - Menu items per status and cause.
  - The pending-change line and its Undo body.
  - The rejected reasons shown.

### 5. Change offering (both modes)

Decision file: frontend.md. Frontend only (it uses slice 4's `pendingChanges`).

- Menu item **Change offering…** (`ui.subscriber.changeOffering`):
  - Offered for status `active` or `suspended`, with no end set and no pending
    `offering` change.
  - Prepaid also needs `paidThrough` present (a product that has started).
- Dialog: an offering select with the same source as Add product (active, sellable,
  recurring price, account currency, current offering excluded), then the date, the
  preview and the submit.
- Body: one `modify` item with `product: {id}`, `productOffering: {id, href, @type:
  ProductOfferingRef}` and `requestedStartDate`. There is never a `status` in the same
  item (a 422 by rule).
- **Postpaid date:** default the product's `nextChargeDate` (the cycle boundary, so no
  proration surprises). Any date today or later is allowed. Explanation: "The old
  offering is billed until {D-1}, the new one from {D}; a charge already billed for the
  period is adjusted by a prorated refund." The preview area shows the new offering's
  first-charge preview from `firstCharge?productOffering=&startDate=D` as information,
  labelled "New offering from {D}".
- **Prepaid date:** fixed, not editable: D = `max(paidThrough, today)`. Explanation: "The
  change applies at the next renewal ({D}) and only when the balance covers the new
  offering's interval; until then the current offering stays." The prepaid preview from
  slice 1 is shown for D (required/available/shortfall).
- The pending change appears on the row through slice 4's line, with Undo.
- Tests: pinned postpaid and prepaid bodies, replayed (postpaid completed with a
  pending change; prepaid completed with a pending change at `paidThrough`). Also: the
  offering list excludes the current offering, the prepaid date cannot be changed, and
  `offeringChangeNotAtRenewal` and other rejections are shown.

### 6. Prepaid commands screen

Decision file: frontend.md. Frontend only.

- Route `/prepaid-commands`, `screens/PrepaidCommands.tsx`, `data-ui="ui.prepaidCommands"`.
  Sidebar: under Billing after Billing runs, label **Prepaid commands**. Until slice 9
  regroups the sidebar, put it after Billing runs in the current list. There is no count.
  It shows a red badge when any command is `failedTechnical`, using
  `?state=failedTechnical&limit=1` and `X-Total-Count`.
- List: `GET /internal/v1/prepaid/command` with `offset`/`limit` paging (page size 50,
  newest first as served).
  - Filters: state (the internal states the contract lists) and account via
    `?account=<id>` (a removable chip with the account name, the pattern of the
    planned `/orders`).
  - Columns: id (display form), kind (order / renewal / transfer), account (link to
    card), state pill, due date, attempts, next retry, result (`outcome`/`reason`, or
    `error` truncated).
- A row opens a dialog from `GET /prepaid/command/{id}`. It shows every field. Links go
  to the order (the order dialog once slice 9 exists, until then the card) and to the
  products. It shows the `needs_topup` block with required/available/shortfall and a
  link to the card. **Retry** (`ui.prepaidCommands.retry`) appears only for
  `failedTechnical`. It calls `POST /prepaid/command/{id}/retry`, then reloads. A 409
  shows the message.
- The subscriber card links to `/prepaid-commands?account={id}` from the prepaid KPI
  area ("Commands").
- Tests: list request and filters, paging, the account chip, the dialog, Retry only for
  `failedTechnical` with its POST pinned (replayed in `test_ui_requests.py`), the badge
  request, and the empty and error states.

### 7. Prepaid browser check and release evidence

Decision file: none (evidence goes in prepaid-release.md).

- Extend `frontend/e2e/smoke.spec.ts` on the seeded stack (`make clean && make up &&
  make seed` if the seed is partial):
  1. Pavel Orlov: open the card, see the Ledger/Available/Paid-through KPIs and at
     least three Balance action rows, and open one detail.
  2. Top up 10.00 by cash, then see the toast and the KPI change.
  3. Add a product starting today and see the prepaid preview. Submit, and wait for
     completion through OrderWatch (the local worker runs, or `make worker-once` from
     the test's setup step if the e2e harness already has a hook for it; otherwise
     assert the `inProgress` watch text only).
  4. Vera Lebedeva: see the funding-suspended product with the needs-top-up text and
     the Top up link.
  5. Open `/prepaid-commands` and see the list.
- The test leaves the data changed. That is accepted because the e2e already runs
  against a disposable seeded stack. State this in the spec file's header comment.
- Update prepaid-release.md: remove the first "Not covered" bullet and add a dated
  "Browser check" paragraph with the `make web-e2e` result.

### 8. Prepaid lifecycle parity in the backend (dated status changes, number changes)

Decision file: prepaid.md. **Decided: build it.** TMF622 gives every item a
`requestedStartDate`, and postpaid honours it for suspend/resume. Prepaid refusing dated
changes is a gap, not a design choice.

- **Dated suspend/resume** (a `modify` item with `product.status`, D > today) on a
  prepaid account. It is accepted the way a dated prepaid termination is (P3g): the
  order and item stay `inProgress`, with one `order` command due on D (the existing
  `prepaid_command` kind `order`; the request JSON carries the item). **No migration
  expected.** If one turns out to be needed (a CHECK on command requests), write a new
  migration. Never edit a shipped one.
- **Acceptance rules** are today's P3f rules evaluated for D, plus the postpaid ordering
  rules:
  - One status change waits at a time (`openModifyItem`).
  - A delete item is refused while one waits (`openModifyItem`).
  - A dated change is refused while a termination is set (`productAlreadyEnded`).
  - `statusUnchanged` is checked against the status that will hold on D (the latest
    pending change, else the current status).
- **Posting on D** (the billing owner, the same transaction pattern as P3g): it runs
  P3f's same-day execution. Suspend sets the manual cause and keeps the paid interval.
  Resume lifts the manual cause, and after expiry it queues the renewal as P3f does. It
  completes the item and order with TMF622/637 events. If the product ended or the
  account is no longer `Active` for a resume by then, the item and order end `failed`
  with a `productOrderErrorMessage` (`productAlreadyEnded` / `accountNotActive`). This
  is the first use of TMF622's `failed` state. Add it to the reachable states in
  `app/ordering/states.py` and to tmf-api.md's "States never reached" note.
- **cancelProductOrder before D** cancels the command (as P3d's pre-grant cancellation
  does) and the order. On or after D, it is the existing same-day undo rule of
  postpaid: allowed on D itself, 409 later.
- **Number-only changes** (a `modify` item with only `realizingResource`) on prepaid are
  accepted and executed exactly as on postpaid: synchronous, the assignment interval
  starting on D, no money, no command. This replaces `prepaidLifecycleUnavailable` for
  that case. Mixed purchase/status orders stay refused.
- `pendingChanges` (slice 4) lists pending dated prepaid status commands.
- **UI:** slice 4's prepaid Suspend/Resume date becomes editable (today or later), with
  the same explanation as postpaid plus "Paid days are not extended by a suspension."
- **MCP:** nothing new (`create_order`). Update the `create_order` tool doc in
  `bss_mcp/` if it states the today-only rule.
- Tests (`tests/test_prepaid_dated_status.py`):
  - Dated suspend: the order stays `inProgress` until D, then the worker tick on D
    completes it and the product is suspended with the paid interval kept.
  - Dated resume after expiry queues a renewal.
  - Cancel before D releases everything. Cancel after D is a 409.
  - `openModifyItem` for both directions.
  - Revalidation `failed`.
  - A prepaid number-only change today and dated.
  - The postpaid suite unchanged.

Not planned: moving an account with history between postpaid and prepaid
([prepaid-plan.md](prepaid-plan.md), "Switching mode").

---

## Phase B

### 9. Sidebar sections and the global Orders screen

Decision: [frontend.md](decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21),
built exactly as written there, with these additions:

- **Billing section order:** Billing runs, Prepaid commands (slice 6), Financial accounts.
- **Order dialog:** for a prepaid `inProgress` order, link to its command in
  `/prepaid-commands` (find it with `?billingAccount.id=` and match the command's order
  link). The subscriber card's "Orders" link goes to `/orders?account=`.

### 10. Order writes: cancel, drafts, order details

Decision file: frontend.md. **This supersedes "No writes in this screen" of the
2026-10-21 decision.** The human delegated it on 2026-10-24, and TMF622 defines these
operations for clients. Record the supersession in that entry. Frontend only.

In the order dialog from slice 9:

- **Cancel order** (`ui.orders.cancel`): offered for `completed` orders (the server
  checks reversibility) and for `draft` and prepaid `inProgress` orders. It posts
  `cancelProductOrder` with only `productOrder` and an optional `cancellationReason`
  from a text field. A 409 shows its reasons.
- **Edit details** (`ui.orders.editDetails`): any order. A PATCH with only changed
  fields among `description`, `priority` (select 0-4, "0 highest") and `category`.
  `externalId` stays API/MCP, because its editor is not worth it.
- **Drafts:**
  - **Save as draft** in Add product (`ui.addProduct.saveDraft`) sends the same body with
    `requestedInitialState: "draft"`. The response is stored as `draft`, and a toast
    links to the order.
  - In the dialog of a draft:
    - **Submit** (`ui.orders.submitDraft`) sends a PATCH with `state: acknowledged`. The
      result is completed, inProgress (prepaid, OrderWatch) or rejected (reasons shown).
    - **Edit draft** (`ui.orders.editDraft`) is offered only for a draft with exactly
      one `add` item. It reopens Add product prefilled and sends a PATCH replacing
      `productOrderItem` and `requestedStartDate`. Drafts with other shapes (from
      API/MCP) show "Edit this draft through the API" and can still be submitted or
      deleted.
    - **Delete** (`ui.orders.deleteDraft`) sends DELETE. It is also offered on `rejected`
      orders (the API allows it there).
- The Orders list gets a `draft` state filter value (already a TMF622 state).
- Tests: every body pinned and replayed; actions offered per state; rejected and 409
  messages; the draft round trip (save, edit, submit).

### 11. Product specifications

Decision file: catalog.md (UI section). Frontend only.

- Catalog screen tabs: **Offerings** (existing) | **Specifications** (`ui.catalog.specifications`).
  Later slices add Categories, Catalogs and Import / export to the same tab bar.
- **List:** name, lifecycle status, brand, version, the number of offerings using it
  (from the offerings already loaded), and last update.
- **New specification** (`ui.catalog.newSpecification`): `name` (required),
  `description`, `brand`, `version`, `lifecycleStatus` (default "In Design"),
  `productNumber`. Characteristics and the other documents stay API/MCP (records only,
  never read by billing).
- **Edit** (`ui.catalog.editSpecification`): a PATCH of only the changed simple fields.
  The lifecycle offers only the forward step, as offerings do.
- **Delete** (`ui.catalog.deleteSpecification`) is offered when no loaded offering uses
  it. The server's 409 still shows.
- **New offering:** next to the specification select, **New specification…** opens the
  same dialog. On success the new spec is preselected.
- Tests: bodies pinned and replayed; the forward-only lifecycle; delete hidden when the
  spec is used; the spec created from New offering is preselected.

### 12. Offering and price detail, edit, delete

Decision file: catalog.md (UI). Frontend only.

- An offering row opens **Offering detail** (`ui.catalog.offeringDetail`) from
  `GET productOffering/{id}`. It shows the fields, prices (with validity), categories,
  specification and code.
- **Edit offering** (`ui.catalog.editOffering`): a PATCH of the changed fields among
  `name`, `description`, `isSellable`, `validFor` (start and end, sent as `zonedDateTime`),
  code (the `offeringCode` entry of `externalIdentifier`, keeping other entries as
  loaded), `productSpecification` (the server refuses after the first order) and
  `category` (multi-select, slice 13 makes categories exist). Lifecycle keeps its
  existing forward button.
- **Delete offering** is offered when it is not `Active`/`Retired`, has no products, and
  its loaded prices are none. The server's 409 still shows, with the text "retire it
  instead".
- **Prices** inside the detail:
  - A price row opens `GET productOfferingPrice/{id}`.
  - **Edit price** sends a PATCH of `name`, `description`, `validFor.endDateTime`, and
    the charge fields only when the server would allow it. The UI shows the server's
    409 "end its validFor and attach a new price" and points to **Change price** (the
    existing internal priceChange) for a live offering.
  - **End price** is a shortcut that PATCHes `validFor.endDateTime` (date picker,
    tomorrow or later).
  - **Delete price** is offered for unattached or never-ordered prices.
- Tests: bodies pinned and replayed; the code edit keeps other identifiers; the
  refusals are shown.

### 13. Categories and product catalogs

Decision file: catalog.md (UI). Frontend only.

- **Categories tab** (`ui.catalog.categories`): a tree by `parent`, roots first, with
  name, lifecycle, the number of offerings and subcategories.
  - **New/Edit:** `name`, `description`, `parent` (select, excluding itself and its
    descendants), `lifecycleStatus` (forward only), `validFor`.
  - Membership: an offering multi-select that writes the category's `productOffering[]`
    (whole-array replacement, as the API defines).
  - **Delete:** offered without subcategories or offerings. The server decides catalog
    membership.
- **Catalogs tab** (`ui.catalog.catalogs`): name, type, lifecycle, root categories.
  - **New/Edit:** `name`, `description`, `catalogType`, `lifecycleStatus`, `validFor`,
    and `category[]` (roots only; a multi-select listing root categories).
  - **Delete:** always offered (nothing refers to a catalog).
- Tests: bodies pinned and replayed; the parent select excludes cycles; membership
  replacement; delete availability.

### 14. Catalog import and export jobs

Decision file: catalog.md (UI). Frontend only.

API facts: synchronous jobs, the document inline as a `data:application/json;base64,...`
URL, and 1 MB cap. Export `query` is empty or `productCatalog.id=<id>`.

- **Import / export tab** (`ui.catalog.jobs`): one list of both job kinds (two requests
  merged, newest first), showing kind, status, creation and completion, and the first
  line of `errorLog`.
- **Export** (`ui.catalog.export`): scope select (Everything | a catalog). It sends a
  POST `exportJob` with `path` set to the TMF620 base path, `contentType:
  application/json` and the `query`. On `Succeeded` it offers **Download** (decode the
  data URL into a Blob with `bss-catalog-{date}.json`) and **Copy as import**.
- **Import** (`ui.catalog.import`): a file input (`.json`) or a pasted JSON textarea.
  The UI checks it is JSON and ≤ 1 MB, encodes it as base64 into the `data:` URL, and
  sends a POST `importJob`. `Failed` shows the full `errorLog` in the dialog.
- **Delete job** (`ui.catalog.deleteJob`) removes the record only. The confirmation
  says "Nothing it imported is undone."
- No polling: the jobs finish within the request.
- Tests: bodies pinned (including the base64 of a small fixture) and replayed (an
  export then an import of it is a no-op success); the size check; the failed error
  log; Download builds the right Blob.

### 15. Parties and customers

Decision file: frontend.md. Frontend only.

- Route `/parties` (`screens/Parties.tsx`, `ui.parties`), sidebar **Customers ▸
  Parties**, with three tabs: **Individuals**, **Organizations**, **Customers**. Each
  uses `getAll` with client-side name search, a status filter where the API has one, and
  `ListState`.
- **Individuals:** name, status, number of customers, linked subscribers.
  **Organizations:** name, status, the same. **Customers:** name, status, engaged party,
  and billing accounts (from the accounts list already loaded through
  `billingAccount?fields=...`, matched by customer id).
- A row opens a detail dialog from the GET. It shows the attributes the subscriber forms
  edit, and links to each subscriber card. **Edit** reuses the existing subscriber form
  pieces (`SubscriberForms.tsx`) for the party or customer alone.
- **Delete** (`ui.parties.delete`): a party without customers and accounts it owns; a
  customer without billing accounts. Hidden when the loaded data shows a dependency.
  The server's 409 still shows.
- A party or customer without an account is now reachable: that is the point of the
  slice.
- Tests: the three list requests, search, links, delete bodies (DELETE, none) pinned
  and replayed, and delete hidden with dependencies.

### 16. Billing account delete; financial account writes and linking

Decision file: accounts.md (it supersedes "UI: read-only" there). Frontend only.

- **Delete subscriber** (`ui.subscriber.delete`) in the card header menu: offered only
  for a postpaid account with no products, charges, bills, payments or transactions in
  what the card loaded. The confirmation says "Deletes the billing account. Its customer
  and party stay (delete them under Parties)." A 409 shows "close it instead". Prepaid
  accounts never show it, because the bucket is history.
- **Financial accounts screen:** **New financial account**
  (`ui.financialAccounts.new`):
  - Fields: `name` (required), owner party (select from parties; the anchor
    `relatedParty` role `owner`), `accountType`, `description`, `creditLimit` (decimal
    string and currency, informational), `state` (default Active).
  - **Edit:** a PATCH of the changed fields. The owner is editable (the server refuses
    while accounts link).
  - **Delete:** offered when `accountRelationship` is empty.
- **Linking:** the subscriber Edit form gets a **Financial account** select listing the
  owner's financial accounts (`financialAccount?relatedParty.id={party}`), plus "None".
  It sends `financialAccount` (ref) or `null` in the billing account PATCH.
- Tests: bodies pinned and replayed (create, edit, link, unlink, delete); the owner
  mismatch 422 shown; delete availability.

### 17. Party and settlement accounts

Decision file: accounts.md (UI). Frontend only.

- The Financial accounts screen becomes **Accounts** with tabs **Financial** (existing),
  **Party**, **Settlement** (`ui.accounts.party`, `ui.accounts.settlement`). The route
  stays `/financial-accounts`. Rename it only if the router already supports aliases.
  Otherwise keep the path and change the title.
- **Party and Settlement tabs:** a list (name, owner, type, state, financial account),
  and New/Edit/Delete with the financial-account form plus a **Financial account** link
  select (same owner). There is no balance column: they have none.
- Delete is always offered (nothing refers to them).
- Tests: bodies pinned and replayed for both resources; the `@type` of each body.

### 18. Billing settings: cycle specifications, bill formats, presentation media

Decision file: accounts.md (UI). Frontend only.

- Route `/billing-settings`, sidebar **Billing ▸ Billing settings**, three tabs.
- **Cycle specifications:** the built-in `monthly` row is shown read-only. Stored ones
  have New/Edit (`name`, `description` only: the policy is fixed monthly, shift 0, as
  the API enforces) and Delete (hidden while the loaded accounts use it).
- **Bill formats** and **presentation media:** New/Edit/Delete with `name` and
  `description` (and whatever other simple string fields the TMF666 `_FVO` defines and
  the API stores; check `app/api/presentation.py`).
- **Subscriber Edit form:** a "Billing" group with selects for cycle specification,
  bill format and presentation media, written through `billStructure` in the billing
  account PATCH, as the API accepts.
- Tests: bodies pinned and replayed for the three resources and the `billStructure`
  PATCH; delete hidden when referenced.

### 19. Payments and refunds history

Decision file: frontend.md. Frontend only.

- **Subscriber card:** a **Payments** panel (`ui.subscriber.payments`) from
  `GET /payment?account.id=` showing date, amount, method, status and reference, with a
  row detail from `GET /payment/{id}`. The existing Recorded refunds panel stays.
- **Global screen** `/payments` (sidebar Billing ▸ Payments, `ui.payments`), tabs
  **Payments** | **Refunds**: `getAll` with a status filter, an account chip
  (`?account=`, sent as `account.id`), a date-range filter applied client side, and
  links to the cards.
- Tests: requests and filters, details, and links.

### 20. Global bills, bill cycles and rates

Decision file: bills.md (UI). Frontend only.

- Route `/bills` (sidebar Billing ▸ Bills, `ui.bills`):
  - Filters: state, account chip (`billingAccount.id`), `billingDate` (exact date
    filter).
  - Columns: bill no., account, billing date, state, amount due, and kind
    (onCycle/offCycle).
  - A row opens the existing bill dialog. The existing state PATCH works there too.
- In the bill dialog, link to **Bill cycle** (`GET billCycle/{id}` from the bill's
  `billCycle` ref, when present) and **Rates**: `GET appliedCustomerBillingRate?bill.id=`
  in a table of period, product, amount and tax. A rate row opens `GET
  appliedCustomerBillingRate/{id}` raw fields.
- **Bill cycles tab:** `list_bill_cycles` with `billingDate` and account columns, and
  the detail from the GET.
- Tests: requests and filters, the dialog links, and the rates table.

### 21. Product and number inventory

Decision file: frontend.md. Frontend only. Writes stay orders.

- Route `/products` (sidebar Products ▸ Products, `ui.products`), using `list_products`
  over every status:
  - Filters: status (server) and account chip (`billingAccount.id`, server). Offering
    and number are filtered client side.
  - Columns: product (display id), offering, account, status, start, end, and number.
  - A row opens a detail from `GET product/{id}`: TMF637 fields, `productOrderItem[]`
    with links to `/orders`, and the realizing resource.
- **Numbers screen:** **Delete number** (`ui.numbers.delete`) for a number never
  assigned (the server refuses with history, and the UI hides it when the loaded data
  shows an assignment). There is a detail dialog from `GET resource/{id}`.
- Tests: requests and filters, the detail, and the delete pinned and replayed.

### 22. Balances screen (TMF654 reads)

Decision file: frontend.md. Frontend only.

- Route `/balances` (sidebar Billing ▸ Balances, `ui.balances`), tabs:
  - **Buckets:** `list_buckets` joined with `list_accumulated_balances` by account.
    Shows account (link), remaining, reserved, and status. The detail comes from both
    GETs.
  - **Top-ups**, **Adjustments**, **Holds**, **Transfers:** `list_topup_balances` etc.
    with a status filter and an account chip (`partyAccount.id`). A row opens the kind's
    GET detail and reuses slice 2/3's detail component, actions included.
- Postpaid accounts never appear, because they have no bucket.
- Tests: the requests per tab, filters, and the detail reuse.

### 23. Event subscriptions (hubs) and System panel

Decision files: events.md and internal-api.md. **This slice includes a backend part
with no migration.**

TMF defines POST/DELETE `/hub` per API and GET only in TMF629, with no list. An operator
screen cannot show what exists without a list. Decision: add one internal read
operation. It is an operator view like the prepaid commands, not a TMF extension.

- `GET /internal/v1/eventSubscription` (internal contract): live hubs (`deleted_at IS
  NULL`) newest first, with `offset`/`limit` and an `api` equality filter. Each item has
  `id`, `api` (TMF number and name), `callback`, `query`, `eventTypes[]`, `subscribedAt`,
  and the delivery summary already stored (last delivery status and time, failures)
  if the delivery tables have it cheaply; otherwise omit it.
- An MCP read tool `list_event_subscriptions`, registered like `list_prepaid_commands`.
- Tests: the list, the filter, deleted hubs excluded, and the auth required.
- **UI** `/integrations` (sidebar: a fourth section **System**, holding Integrations
  and Users (admin)):
  - The list from the internal operation.
  - **New subscription:** API select (the eight APIs), callback URL, and event types (a
    multi-select from the API's listener events, built from the same list the server
    validates; hard-code it in `frontend/src/api/hubs.ts` with a test that compares it
    with `app/events/types.py` through `tests/test_ui_requests.py`). It POSTs to that
    API's `/hub` with `callback` and `query` = `eventType=A,B` (omitted for all events).
  - **Delete** sends a DELETE to the API's `/hub/{id}`.
  - The server's 422 for a non-public callback is shown as is.
  - Visible to every role, as the API is (users.md U4).
- **System panel** on the same screen (`ui.system`):
  - `GET /billing-status` (the worker's status, shown as key/value).
  - `GET /healthz` (OK or the error).
  - A link to `/tmf-api/schema/BssBillingAccount.schema.json` (opens raw JSON in a new
    tab).
- Tests: the backend list; the UI create/delete bodies pinned and replayed for two APIs
  (one v4: TMF676 EventSubscription, one v5); the event list matches the server; the
  status panel renders.

### 24. Parity check

Decision file: harness.md (or mcp.md).

- `scripts/ui_mcp_parity.py`, run by a backend test
  (`tests/test_ui_mcp_parity.py`):
  - It lists the MCP operations (`api_operations(app.openapi())`).
  - It extracts every (method, path template) the frontend calls from
    `frontend/src/**/*.ts(x)` outside tests and generated code. A regex over the path
    constants and the `request`/`getJson`/`getAll`/`postJson`/`patchJson` calls is
    enough. Normalize `${...}` segments to `{}`.
  - It fails listing each operation with no UI call and no allowlist entry.
- Allowlist `tests/ui-mcp-allowlist.txt`, one line per operation with its reason:
  - `create_product`/`patch_product`/`delete_product`: orders.
  - `get_bill`: the internal bill detail.
  - `get_hub_customer`: the internal list covers it.
  - Plus anything the earlier slices deliberately left out (for example `externalId` is
    not an operation, so it needs no entry).
- Then mark [ui-mcp-gap-plan.md](ui-mcp-gap-plan.md) closed, with a pointer here.
