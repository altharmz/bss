# Admin UI (stage 5)

A desktop admin console for billing operators: `frontend/` (Vite + React + TypeScript),
built to static files and served by the shared Caddy on the same origin as the API.
The app authenticates operators with sessions; removing Caddy's outer basic-auth prompt
remains deployment slice 3 ([decisions/users.md](decisions/users.md)).
The design handoff it implements is `design-ref/design_handoff_bss_admin/` (a read-only
HTML prototype and README); where the build differs from the design, the decision is
recorded in [decisions/frontend.md](decisions/frontend.md), "Frontend (stage 5)
decisions", and summarised below.

Progress comes from `data-ui` markers in `frontend/src`: see [PROGRESS.md](PROGRESS.md).
Tested means a passing component test saw the marker on screen (`expectUi`, see "Tests").

## Architecture

| Part | Where | What |
|---|---|---|
| Routing | `frontend/src/app/router.tsx` | a minimal History-API router: `/subscribers`, `/subscribers/{accountId}`, `/catalog`, `/billing-runs` (not `/billing`, so it can never be confused with the app's `/billing-status`); `/` redirects to `/subscribers`; anything else is the not-found page. Deep links work because Caddy answers unknown paths with `index.html` |
| Shell | `frontend/src/app/Shell.tsx` | sidebar (brand, environment tag, navigation "Main" in three sections, operator), top bar (breadcrumbs, business date, time zone) |
| System context | `frontend/src/app/context.tsx` | loads `GET /internal/v1/context` before any screen: no date can be shown without the business date and zone |
| API client | `frontend/src/api/client.ts`, `bss.ts` | fetch wrapper; every failure is an `ApiError` whose message is the call ("GET /path -> 503 Service Unavailable"); `getAll` pages by 100; `useResource` = loading / error / ok + retry; `usePolled` = the same, refreshed in the background every few seconds while a predicate on the data holds (a failed refresh keeps the data and stops) |
| Types | `frontend/scripts/gen-types.mjs` -> `src/api/generated/` (git-ignored) | generated from `spec/` (TMF620, 622, 637, 666 and the internal API) on every build, lint and test - never from FastAPI's `/openapi.json` |
| UI kit | `/ui-kit`, `frontend/src/ui/` | Ledger v2 buttons (busy and outcome labels), Field/TextInput/SelectField/DateField/MoneyField/Checkbox, filter and radio Segmented, live controls gallery including every D4 status via explicit StatusPill kinds, Tag/Flag/Badge variants, neutral-positive Amount figures and copyable Id examples; all status callers use explicit D4 kinds; default/compact/dense selected tables and loading/error/empty/no-results previews; keyboard Tabs and TabPanel, open Menu with disabled/negative items and separators, structured table range and paging previews; KeyValue definition grid, prepaid/postpaid MoneyStrip, PageHeader slots and titled/untitled Panel previews |
| Money | `frontend/src/format/money.ts` | amounts become integer cents at the edge; no float arithmetic; typed amounts are sent as the validated decimal string as an exact JSON number (`jsonWithAmount`); negatives use U+2212 |
| Dates | `frontend/src/format/dates.ts` | business dates in BILLING_TZ via `Intl`, never the browser's zone; dates sent carry BILLING_TZ's offset *for that date* (`zonedDateTime`) |

**Every list renders through `ListState`** (`frontend/src/ui/states.tsx`): skeleton rows
while loading, an error block naming the failed call with Retry, an empty state, and "No
matches" with Reset filters when filters hide everything (`frontend/src/ui/states.test.tsx`).

## Screens and actions

`data-ui` is the marker the progress tracker looks for; the API column lists every
operation the screen calls (`frontend/src/api/bss.ts`).

### Shell (`ui.shell`)

Ledger v2 slice 4: the 216 px subtle sidebar has a BSS Admin brand/environment Tag,
28 px navigation items and a hairline active item. The footer trigger
(`ui.shell.operator`) contains login initials, login and role, preserving the operator
menu. The 48 px top bar holds breadcrumbs and the segmented business-date chip
(`ui.shell.businessDate`). Drift uses the context instant in its business zone, with
warning styling and calendar-day ahead/behind wording. No display-name field exists,
so the footer uses the session login and its first two characters.

The failed-run indicator uses the shared failed Badge.

Sidebar with the navigation (Subscribers, Catalog, Billing runs - each with its count;
Billing runs shows the red **failed** badge instead when the latest run failed), the
environment (`APP_ENV`) and the signed-in operator and role; top bar with breadcrumbs,
the business date and the UTC offset. The Billing runs screen asks the sidebar to reload
its run count and badge whenever the latest run it shows changes.
API: `GET /internal/v1/context`, `GET /internal/v1/billingRun?limit=1` (the count is its
`X-Total-Count`).

The sidebar is one `<nav aria-label="Main">` with four labelled sections
(`ui.shell.section.customers|products|billing|system`): Customers (Subscribers, Parties), Products (Product
catalog, Orders, Numbers) and Billing (Billing runs, Prepaid commands, Accounts,
Bills, Payments, Balances, Billing settings, without a count or count request) and System (Integrations, and Users for an admin). The Orders count is `productOrder?limit=1&fields=id`
(`X-Total-Count`). Design: [frontend.md](decisions/frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21).

### Sign-in, operator menu and users

The UI checks `GET /internal/v1/session` before loading the shell, business context or
any screen. An expired session opens `/sign-in`, preserving the requested local path
and query for after sign-in. Initial passwords and admin resets force Change password
before any protected screen. Password changes revoke the current session too, so the
operator signs in again. Passwords stay in form state; server failures clear password
fields and closing a form discards them. No token or password is kept in browser storage.

The sidebar operator menu shows username and role, Change password and Sign out; admins reach
Users in the System section. `/users` lists username, role, active state and last sign-in in the business
time zone. New user sets an initial password and role; Edit sends only changed role and
disabled fields; Reset password forces another first change. Self-disable is unavailable.
The server enforces the last-active-admin rule and displays its rejection in the form.
A non-admin opening `/users` sees Not allowed without a users-list request.

Session and user writes send `X-Bss-Request: 1`. User PATCH bodies use
`application/json`, as the internal specification requires; TMF PATCH continues to use
the existing merge-patch helper. Component tests pin both bodies and headers, with
real-request counterparts in `tests/test_ui_requests.py`.

Browser tests use `tests/e2e_users.py` to prepare random local-only admin and user
identities. The ordinary seed still creates no users. Credentials live only in ignored
`frontend/.e2e/credentials.json`; cleanup disables the fixture users, revokes sessions
and removes that file. The browser exercises forced password change, admin forms,
ordinary-user denial and sign-out as well as the existing billing screens.

### Subscribers list (`ui.subscribers.list`) - `screens/Subscribers.tsx`

Ledger v2 slice 5: PageHeader shows the filtered count and New subscriber action. The
300 px search (Name, BA id or subscriber no.) has an icon and `/` focus shortcut outside
editable fields. Account state and mode filters retain counts; Reset filters appears when
active. Negative balance N stays at the right. The page table shows Name, copyable BA Id,
account StatusPill, Mode, Products, Cycle day (— for prepaid), and Balance EUR without a
currency suffix. Over limit appears strictly below the negative credit limit. Query-aware
no-results names the active Status and Mode. A row opens the card; reads are unchanged.
API: `GET /tmf-api/accountManagement/v5/billingAccount` (all pages),
`GET /tmf-api/productInventory/v5/product?status=active&fields=billingAccount` (all pages).

### New subscriber (`ui.subscribers.new`) and Edit subscriber (`ui.subscriber.edit`) - `screens/SubscriberForms.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

A subscriber is three TMF resources; **there is no delete** (an account carries products,
bills and payments - it can be Closed). **+ New subscriber** (list header): a person
(given + family name) or an organization (name), the account name (default "<name>
main"), billing cycle day 1-28 (default 1) and an optional credit limit; the currency is
the UI currency. Three calls in reference order: the party (TMF632), the customer engaging
it with status **Active** (TMF629), the billing account (TMF666, `BssBillingAccount`, the
credit limit as an exact number token). A failure stops there and names what was already
created - nothing is undone behind the operator's back, as in New offering. On success the
new card opens. **Edit** (card header) loads the party, customer and account and offers
the names, the customer status (Created / Active / Inactive), the account name, the
account state (Active / Suspended / Closed, with a note that a non-Active account takes no
new orders while its products keep billing) and the credit limit (empty = none). Save
sends merge patches of **only what changed**, party, then customer, then account
(`creditLimit` as an exact token, or `null` to remove it); a failure names what was
already saved. The cycle day and the currency cannot be changed (the backend refuses).
API: `POST/PATCH /tmf-api/partyManagement/v5/{individual|organization}`,
`POST/PATCH /tmf-api/customer/v5/customer`,
`POST/PATCH /tmf-api/accountManagement/v5/billingAccount`.

### Subscriber card (`ui.subscriber.card`) - `screens/SubscriberCard.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: account/product StatusPill, copyable BA Id in the meta line, and neutral-positive movement Amounts. Product actions use the shared keyboard Menu, retaining labels and markers.

Header with name, state, subscriber number, billing account (`BA-xxxxxxxx`), customer
status, and **+ Add product** (disabled for a non-Active account, with the reason - the
same rule ordering enforces). Billing account panel: balance, currency, cycle day (and the
next cycle), credit limit, available incl. credit. Active products (offering, code,
specification, activated, next charge, price, status note for `created` /
`pendingTerminate`), charge history (period, "N of M" days when prorated, amount) and
transaction history (signed amount, balance after).
API: `GET .../billingAccount/{id}`, `GET /tmf-api/customer/v5/customer/{id}`,
`GET /internal/v1/billingAccount/{id}/products`, `.../charges?limit=20`,
`.../transactions?limit=20`.
**Delete** (`ui.subscriber.delete`, confirmation `ui.subscriber.deleteConfirm`) is in the header for
a postpaid account whose loaded products, charges, transactions and bills are all empty:
`DELETE .../billingAccount/{id}`, then the Subscribers list; a 409 says to close it instead. The
Edit form's **Financial account** select (`ui.subscriber.financialLink`, from
`GET .../financialAccount?relatedParty.id=`) links or unlinks (`financialAccount` ref or `null` in
the PATCH). Design: [next-slices-design.md](next-slices-design.md) section 16.

### Balance actions (`ui.subscriber.prepaid.history`, `ui.subscriber.prepaid.historyDetail`) - `screens/PrepaidHistory.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: balanceAction StatusPill keeps custom labels; signed Amounts stay neutral for credits.

Prepaid cards only, above Charge history. Lists the account's top-ups, holds, adjustments and
transfers (requested time, action, signed amount, status, note), newest first; the account's
own actions and the transfers it received are merged and de-duplicated. A row opens a dialog
fetched fresh: id, kind, status, amount, dates, reason, description, payment method, hold expiry,
sender and receiver with links to their cards, transfer cost and who pays it, product. Reloads
with the card. Transaction history also lists Adjustment, Reversal, Transfer in/out.
API: `GET /tmf-api/prepayBalanceManagement/v4/balanceActionHistory?partyAccount.id=` and
`?receiverBucket.id=`, `.../balanceActionHistory/{id}`, `.../bucket/{id}` (receiver name).

The dialog also writes (`ui.subscriber.prepaid.cancelTopup`, `cancelAdjustment`, `releaseHold`,
`removeFailed`, `editNote`): Cancel top-up / Cancel adjustment (completed) and Release hold
(created) each ask Yes/No and send `PATCH {topupBalance|adjustBalance|reserveBalance}/{id}`
`{"status":"cancelled"}` (a hold also repeats its `reason`); Remove (failed, any kind) sends
`DELETE .../{id}`; Edit note (not failed, not transfers) sends the status and the changed
`reason`/`description`. A 409 is shown in the dialog; success toasts and reloads the card.

### Deactivate a product (`ui.subscriber.deactivate`)

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

A destructive button per product with an inline confirm. It places a **TMF622 order with
one delete item, effective today** (no `requestedStartDate`): a started product ends today
(not billed from today); one not started yet is cancelled. A rejected order shows its
reasons.
API: `POST /tmf-api/productOrderingManagement/v5/productOrder`.

### Product actions (`ui.subscriber.productActions`) and pending changes

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

**Actions ▾** on a product row lists what the row allows: **Suspend…** (`ui.subscriber.suspend`,
active, no pending status change), **Resume…** (`ui.subscriber.resume`, suspended but not by
funding or the account, no pending status change) and **End on a date…**
(`ui.subscriber.endOnDate`, no end set). Each opens a dialog (`ui.subscriber.productChange`)
with a date and an explanation, and places one TMF622 order: a `modify` item with
`product.status` (suspend, resume) or a `delete` item, `requestedStartDate` noon of the date.
Dates start at today in both modes. A prepaid Suspend/Resume dated later stays `inProgress` until its day (the worker applies it) and adds "Paid days are not extended by a suspension." A rejected order shows
its reasons in the dialog; an `inProgress` one is followed like any prepaid order. Pending
changes (`pendingChanges` of the internal products) show under the product ("Suspends on ...",
"Changes to X on ... (waits for funds)") with **Undo** (`ui.subscriber.undoChange`: a
`cancelProductOrder` of that order; a 409 shows in the row). A funding-suspended prepaid product
shows its shortfall and **Top up** (`ui.subscriber.topUpProduct`). Deactivate stays beside the menu.
API: `POST .../productOrder`, `POST .../cancelProductOrder`,
`GET /internal/v1/billingAccount/{id}/products`.

**Change offering…** (`ui.subscriber.changeOffering`, active or suspended, no end, no pending
offering change; prepaid needs `paidThrough`) opens `ui.subscriber.offeringChange`: pick an
active offering (not the current one), see the first-charge preview for the date, submit one
`modify` item with the new `productOffering` (no status). Postpaid: date from today, default the
next charge date; the old offering is billed until D-1 and the new one from D. Prepaid: the date
is fixed to `max(paidThrough, today)`, the change applies at a funded renewal and shows as a
pending change until then (Undo). API adds `GET /internal/v1/billingAccount/{id}/firstCharge`.

### Bills (`ui.subscriber.bills`), bill dialog (`ui.subscriber.billDetail`), hold/release (`ui.subscriber.billHold`), record payment (`ui.subscriber.recordPayment`) - `screens/Bills.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: bill StatusPill; hold actions use quiet negative buttons.

The bill lifecycle (docs/archive/bill-lifecycle-design.md section 6). The Billing account panel
adds **Due on bills** (outstanding, with "N overdue · amount" in red and "N on hold") and
**Unused credit**. The **Bills** panel lists the account's bills newest first: bill no.
(`BL-xxxxxxxx`), bill date, due date ("overdue" when due before the business date with
something remaining; "credit note" instead of a date for a negative bill), amount due,
remaining, state pill (settled green, sent / partiallyPaid amber, onHold red, new /
validated blue - existing token colours only). A row opens the **bill dialog**: state,
period, due date, amounts, the charges on the bill, what paid it (payments and credit
notes, automatic or explicit, with the time), its state history (cause and operator), and
**Put on hold** / **Release** with an inline confirm - offered exactly as the server's
`allowedStates` says (release sends the state the allocations imply). A change closes the
dialog, toasts and reloads the card. **Record payment** (header button, any account
state): amount, method (bankTransfer / card / cash / Other with a name), and optionally
**Apply to specific bills** - the open bills (not on hold) with amounts prefilled oldest
first up to the payment, editable; more than a bill's remaining amount, or more than the
payment in total, is refused before anything is sent. Every amount goes out as an exact
JSON number token (`jsonWithAmounts`). The toast says how many bills it paid now, or that
it is kept as credit. The UI never allocates anything itself.
API: `GET .../customerBill?billingAccount.id=` (TMF678, all pages),
`GET /internal/v1/billingAccount/{id}/settlement`, `GET /internal/v1/customerBill/{id}`,
`PATCH /tmf-api/customerBillManagement/v5/customerBill/{id}` (`{"state": ...}`, merge
patch), `POST /tmf-api/payment/v4/payment` (`paymentItem[]` when lettered).

### Refunds and on-demand bills - `screens/AccountActions.tsx`

**Refund unused credit** sits beside Unused credit. Refundable credit is the lesser
of unused credit and positive account balance; the server rechecks it when saving.
The dialog records an already completed payout with amount, method and an optional
description, including for Closed and Suspended accounts. It does not send money.
The card reloads after recording; **Recorded refunds** shows payout dates, amounts,
methods and descriptions, while Transaction history retains **Refund paid out**.

**Bill now** in the Bills heading queues a request, with no amount or date input.
The confirmation explains that processing waits for a successful account run from
tomorrow onward. **Bill requests** shows progress, terminal states and explanations;
**Open bill** opens the resulting bill in the existing dialog. One pending request
disables another submission. Status refreshes every five seconds for up to three
minutes, then offers manual Refresh. Uncertain writes are never automatically retried;
the dialog asks the operator to check the recorded refund or request first.

API: GET/POST `/tmf-api/payment/v4/refund` (`account.id` list filter), GET/POST
`/tmf-api/customerBillManagement/v5/customerBillOnDemand` (`billingAccount.id`
list filter). Both lists read all pages. Markers: `ui.subscriber.recordRefund`,
`ui.subscriber.refunds`, `ui.subscriber.billNow`, `ui.subscriber.billRequests`.

### Add product (`ui.subscriber.addProduct`, `ui.addProduct.saveDraft`, `ui.addProduct.saveDraftChanges`) - `screens/AddProduct.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Modal: 1. pick an Active, sellable offering (filterable list with code, specification and
today's price); 2. start date (min = business date); 3. **first-charge preview computed by
the backend** - period, billable days of period days, price, amount, when it is charged
and billed, the following full period, balance after, and a credit-limit warning that
never blocks. Confirm places a TMF622 add order; a rejection shows its reasons. The copy
follows the end-of-day rule: nothing is "charged on confirmation" - an order completed
today is charged by the run of max(start, tomorrow).
On a **prepaid** account (`ui.addProduct.prepaidPreview`) the preview shows period, price,
"paid in full in advance", **To pay now** (or **To pay on {date}**), Available, Shortfall
(only when > 0) and Ledger after (start today only). A shortfall with a start today adds a
warning and **Top up {n}** (`ui.addProduct.topUpShortfall`), which closes this dialog and
opens the card's Top-up form with the amount typed in; a future start only shows a hint.
Submit stays enabled; the server's refusal shows as for any rejected order.
API: `GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true`,
`GET .../productOfferingPrice?priceType=recurring`,
`GET /internal/v1/billingAccount/{id}/firstCharge?productOffering.id=&startDate=`,
`POST .../productOrder` (`requestedStartDate` = noon of the date in BILLING_TZ).

### Product catalog (`ui.catalog.list`) - `screens/Catalog.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: lifecycle StatusPill and quiet negative destructive actions.

Table of offerings: code, name, specification, the price valid now in the UI currency
(or the next one with its start), status, validity. Search by code or name; filter by
lifecycle status. **+ New offering**. A row opens the offering detail.
API: `GET .../productOffering`, `GET .../productSpecification`,
`GET .../productOfferingPrice?priceType=recurring` (all pages).

### Specifications tab (`ui.catalog.specifications`), `ui.catalog.newSpecification`, `ui.catalog.editSpecification`, `ui.catalog.deleteSpecification` - `screens/Specifications.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: lifecycle StatusPill and quiet negative destructive actions.

Second tab of the Catalog screen: specifications with status, brand, version, the number of
offerings using each and the last update; search by name or brand. **+ New specification**
and a row's dialog edit name, description, brand, version, product number and the lifecycle
(forward step only); Delete is offered only when no loaded offering uses it (the server's 409
still shows). **New offering** has **New specification...**, which preselects the new one.
API: `POST|PATCH|DELETE .../productSpecification` (merge patch of changed fields only).

### Categories and Catalogs tabs (`ui.catalog.categories`, `ui.catalog.catalogs`) - `screens/Classification.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: lifecycle StatusPill; category indentation uses a runtime CSS variable.

**Categories** is a tree by parent (roots first, indented) with status, offering count and
subcategory count; search by name. **+ New category** (`ui.catalog.newCategory`) and a row's dialog
(`ui.catalog.editCategory`) edit name, description, parent (the select leaves out the category and
everything below it), lifecycle (forward step only), validity (an end needs a start) and the
offering membership (a checkbox list; a changed membership is sent as the whole array). Delete
(`ui.catalog.deleteCategory`) is offered without subcategories and offerings; the server's 409
(catalog membership) still shows. **Catalogs** lists name, type, status and root category names;
**+ New catalog** (`ui.catalog.newCatalog`) / edit (`ui.catalog.editCatalog`) take name, description,
type, lifecycle, validity and the root categories (roots only); Delete (`ui.catalog.deleteCatalog`)
is always offered.
API: `POST|PATCH|DELETE .../category`, `.../productCatalog` (merge patch of changed fields only).

### Import / export tab (`ui.catalog.jobs`) - `screens/CatalogJobs.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: quiet negative destructive actions and CSS-based form styling.

One list of import and export jobs (newest first): kind, status, created, completed and the first
line of the error log; a row opens the job (`ui.catalog.job`, full error log, Download for a
succeeded export, Delete). **Export...** (`ui.catalog.export`) takes a scope (Everything or one
catalog) and offers **Download** (`bss-catalog-<date>.json`) and **Copy as import** on success.
**Import...** (`ui.catalog.import`) takes a `.json` file or pasted JSON (checked: valid JSON,
at most 1,000,000 bytes) and shows a failed job's whole error log in the dialog
(`ui.catalog.errorLog`). Delete (`ui.catalog.deleteJob`) removes the record only: "Nothing it
imported is undone." No polling: a job finishes within its request.
API: `GET|POST|DELETE .../importJob`, `.../exportJob`.

### Offering detail (`ui.catalog.offeringDetail`), lifecycle step (`ui.catalog.lifecycle`), change price (`ui.catalog.changePrice`)

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

The detail dialog shows the offering and its recurring prices. The lifecycle button offers
**only the next forward step** (In Design -> Active -> Retired) with an inline confirm
(the rule itself is the backend's). **Change price** appears only when the offering has an
open-ended price in the UI currency; it calls the atomic price change, and its copy says
existing products pick up the new price from their next billing period starting on or
after the date, new orders from the date.
API: `PATCH .../productOffering/{id}` (`application/merge-patch+json`,
`{lifecycleStatus}`), `POST /internal/v1/productOffering/{id}/priceChange`.

The dialog also loads `GET .../productOffering/{id}` and shows description, sellable, code,
specification and categories. **Edit offering** (`ui.catalog.editOffering`) patches only the
changed fields among name, description, sellable, validity, code (the `offeringCode` entry;
other identifiers are sent as loaded), specification and categories (checkbox list). **Delete**
(`ui.catalog.deleteOffering`, inline confirm) is offered for an In Design offering with no
attached price; the server's 409 shows. A price line opens the price dialog
(`ui.catalog.priceDetail`): edit name, description and amount (`ui.catalog.editPrice`; a 409
points to Change price), **End price** (`ui.catalog.endPrice`, from tomorrow) and **Delete**
(`ui.catalog.deletePrice`). API: `GET|PATCH|DELETE .../productOfferingPrice/{id}`,
`DELETE .../productOffering/{id}`, `GET .../category`.

### New offering (`ui.catalog.newOffering`)

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Modal: code (upper-cased), name, specification, monthly price, valid from, valid to
(inclusive in the UI, sent as the next midnight). Creating is **two TMF620 calls**, as the
spec models it: the price first, then the In Design offering referencing it; if the second
fails the unattached price stays (it can never price anything) and the error is shown.
API: `POST .../productOfferingPrice`, `POST .../productOffering`.

**The write requests are pinned on both sides**: component tests assert the exact bodies
(`frontend/src/screens/*.test.tsx`), and `tests/test_ui_requests.py` replays the same
bodies against the real API.

### Products (`ui.products`, `ui.products.status`, `ui.products.accountChip`, `ui.products.detail`, `ui.products.orderItems`, `ui.products.resource`) - `screens/Products.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: explicit product StatusPill.

Route `/products` (sidebar Products). TMF637 `product` over every status: server filters status and
`?account=` chip, client filters offering and number. Columns: product, offering, subscriber, status,
start, end, number. A row opens the detail (`GET product/{id}`): dates, order items with a link to
Orders, the realizing resource. Read-only; orders alone change products. Slice 21.

### Orders (`ui.orders`, `ui.orders.accountChip`, `ui.orders.detail`, `ui.orders.items`, `ui.orders.cancellations`, `ui.orders.command`, `ui.orders.errors`) - `screens/Orders.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: explicit order StatusPill.

Route `/orders`. The list is read-only, the dialog writes (slice 10, below). TMF622 `productOrder` pages of 50 as served (oldest first), state
filter, `?account=<id>` chip (the card's "Orders" link `ui.subscriber.orders`). Columns: order id,
subscriber (links to the card), state, item actions, requested start, created, completed or
cancelled. A row opens the dialog: header fields, items (action, offering, product, state,
tax-included price), `productOrderErrorMessage`, the order's `cancelProductOrder` rows, and for an
`inProgress` order with a prepaid command a link to `/prepaid-commands?account=`. Design:
[next-slices-design.md](next-slices-design.md) section 9.

Dialog writes (`ui.orders.cancel`, `ui.orders.editDetails`, `ui.orders.submitDraft`,
`ui.orders.editDraft`, `ui.orders.deleteDraft`, `ui.orders.draftViaApi`; design section 10): **Cancel
order** (completed, draft, inProgress) posts `cancelProductOrder` with the order and an optional
reason, a 409 shows its reasons; **Edit details** PATCHes only the changed fields of description,
category and priority (0 highest; a cleared one is sent as null); on a **draft**: **Submit** PATCHes
`state: acknowledged` (completed, inProgress followed by the order watch above the list, or
rejected with its reasons in the dialog), **Edit draft** (one `add` item only; other shapes show
"Edit this draft through the API") reopens Add product prefilled and PATCHes `productOrderItem` and
`requestedStartDate`, **Delete** (also on rejected orders) sends DELETE. After an uncertain
result (no response or 5xx) the dialog says it is unsure and offers Refresh.

### Prepaid commands (`ui.prepaidCommands`, `ui.prepaidCommands.accountChip`, `ui.prepaidCommands.detail`, `ui.prepaidCommands.retry`) - `screens/PrepaidCommands.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: command StatusPill uses the actual command state.

Route `/prepaid-commands`, sidebar after Billing runs with a red badge while any command is
`failedTechnical`. Lists the internal prepaid command journal (id, kind, account, state, due date,
attempts, next retry, result), 50 per page, filtered by state and by `?account=<id>` (a removable
chip; the card's "Commands" link, `ui.subscriber.prepaid.commands`, sets it). A row opens every
field and the `needs_topup` block (`ui.prepaidCommands.needsTopup`); **Retry** appears only for
`failedTechnical` (POST `.../retry`, no body; a 409 is shown). Design:
[next-slices-design.md](next-slices-design.md) section 6.

### Parties (`ui.parties`, `ui.parties.individuals|organizations|customers`) - `screens/Parties.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: separate party and customer StatusPill kinds.

Route `/parties`, sidebar **Customers > Parties** (no count request). Three tabs: Individuals,
Organizations (name, status, number of customers, linked subscribers) and Customers (name,
status, engaged party, billing accounts), each with a name search and a status filter, so a party
or customer without a billing account is reachable. Subscriber links go to the card. A row opens
the record read with GET (`ui.parties.individual|organization|customer`): the attributes the
subscriber forms edit. **Edit** (`ui.parties.edit`) reuses the subscriber form fields for the
party names or the customer name and status alone (merge patch of changed fields). **Delete**
(`ui.parties.delete`, a confirmation) is offered unless the loaded data shows a dependency: a
party with customers or owning a financial, party or settlement account, a customer with billing
accounts (the dialog says what blocks it); the server's 409 still shows. After an uncertain result
the dialog offers Refresh. API: `GET .../individual|organization`, `GET .../customer?fields=...`,
`GET .../billingAccount?fields=name,relatedParty`, `GET .../financialAccount|partyAccount|
settlementAccount?fields=relatedParty`; `PATCH|DELETE` on the three. Design:
[next-slices-design.md](next-slices-design.md) section 15.

### Financial accounts writes (`ui.financialAccounts.new|edit|delete`) - `screens/FinancialForms.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

The Financial accounts screen has **+ New financial account** (name, owner from the individuals and
organizations, account type, description, informational credit limit and currency, state), a row
**Edit** (merge patch of changed fields, owner editable, the server refuses while accounts link)
and **Delete** (only when nothing is linked; confirmation). Server refusals show in the dialog;
after an uncertain result it offers Refresh. API: `POST|PATCH|DELETE .../financialAccount`,
`GET .../individual|organization`. Design: [next-slices-design.md](next-slices-design.md)
section 16.

### Accounts: party and settlement tabs (`ui.accounts.party|settlement`) - `screens/FinancialAccounts.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: account StatusPill and neutral-positive Amount figures.

The Financial accounts screen is now **Accounts** (route `/financial-accounts`, kept) with tabs
**Financial** (above), **Party** and **Settlement**. A party or settlement tab lists name, owner,
type, state and financial account (no balance: they have no ledger) with **+ New party|settlement
account**, row **Edit** and **Delete** (always offered; the server's refusal still shows). The form
is the financial-account form plus a **Financial account** select of the owner's financial accounts
(plus None; a new owner resets it). Markers `ui.accounts.party|settlement` with `.new`, `.form`,
`.edit`, `.delete`, `.confirmDelete`, `.financialLink`. API: `GET|POST|PATCH|DELETE
.../partyAccount|settlementAccount`, `GET .../financialAccount?relatedParty.id=`. Design:
[next-slices-design.md](next-slices-design.md) section 17.

### Billing settings (`ui.billingSettings.cycles|formats|media`) - `screens/BillingSettings.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: built-in settings use an attribute Tag; destructive actions use quiet negative buttons.

Route `/billing-settings`, sidebar Billing > Billing settings, three tabs: **Cycle specifications**,
**Bill formats**, **Presentation media**. Each lists name, description and how many loaded billing
accounts use it, with **+ New ...**, row **Edit** and **Delete**. Delete is hidden while a loaded
account names the setting; the server's 409 still shows in the dialog. The built-in `monthly`
specification is shown read-only. Cycle specifications and media take `name` and `description`; a
bill format's name is one of `JSON detailed` / `JSON summary` (a select: the API stores only those).
The subscriber **Edit** form has a **Billing** group (`ui.subscriber.billingGroup`) with selects for
the cycle specification and bill format and checkboxes for the media, sent as only the changed parts
of `billStructure` in the billing account PATCH (media as a whole list, the built-in cycle as id
`monthly`). Markers `ui.billingSettings.<tab>` with `.new`, `.form`, `.edit`, `.delete`,
`.confirmDelete`. API: `GET|POST|PATCH|DELETE .../billingCycleSpecification|billFormat|
billPresentationMedia`, `PATCH .../billingAccount/{id}`. Design:
[next-slices-design.md](next-slices-design.md) section 18.

### Bills (`ui.bills`) - `screens/GlobalBills.tsx`, `screens/BillLinks.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: bill StatusPill; hold actions use quiet negative buttons.

Reads plus the existing bill dialog. Route `/bills`, sidebar Billing > Bills (no count): tabs **Bills** |
**Bill cycles**. Bills: bill no., subscriber (link), billing date, state, amount due, kind (`runType`:
onCycle/offCycle); filters: state (server), account chip (`?account=<id>`, sent as `billingAccount.id`,
`ui.bills.accountChip`) and a billing date applied client side. A row opens the bill dialog (hold/release as
on the card), whose **Related** section (`ui.bills.links`) has **Bill cycle** (`GET billCycle/{id}`,
`ui.bills.cycleDetail`) and **Rates** (`GET appliedCustomerBillingRate?bill.id=`; a row opens
`ui.bills.rateDetail`, the raw GET). The Bill cycles tab lists `GET billCycle` (name, billing date, payment
due) with the same date filter and a detail dialog. API: `GET .../customerBillManagement/v5/customerBill|billCycle|
appliedCustomerBillingRate`. Design: [next-slices-design.md](next-slices-design.md) section 20.

### Payments (`ui.payments`, `ui.subscriber.payments`) - `screens/Payments.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: payment StatusPill for payments and refunds.

Reads only. The subscriber card has a **Payments** panel (`ui.subscriber.payments`: date, amount,
method, status, reference = `correlatorId`, else `authorizationCode`, else `name`), newest first,
above Recorded refunds; a row opens the payment read with `GET /payment/{id}`
(`ui.payments.detail`: total, net, tax, method, references, and the bills it paid). Route
`/payments`, sidebar Billing > Payments (no count): tabs **Payments** | **Refunds**, a status filter
(`done`, the only status), an account chip (`?account=<id>`, sent as `account.id`,
`ui.payments.accountChip`) and From/To dates applied client side to the loaded rows (business dates).
Subscribers link to their cards. API: `GET .../payment/v4/payment|refund?account.id=&status=` (paged
100 at a time), `GET .../payment/{id}`. Design: [next-slices-design.md](next-slices-design.md)
section 19.

### Balances (`ui.balances`, `ui.balances.buckets|topups|adjustments|holds|transfers`, `ui.balances.bucketDetail`) - `screens/Balances.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: bucket and balanceAction StatusPill retain the action-specific note labels.

Reads only, TMF654. Route `/balances`, sidebar Billing > Balances (no count). Tabs **Buckets** |
**Top-ups** | **Adjustments** | **Holds** | **Transfers**, each with a status filter
(`ui.balances.status`; buckets: active, suspended, expired; actions: created, completed,
cancelled, failed) and an account chip (`?account=<id>`, `ui.balances.accountChip`, sent as
`partyAccount.id`). Buckets: `GET bucket` joined with `GET accumulatedBalance` by account
(Subscriber, Remaining, Reserved, Total, Status); a row opens the bucket detail
(`ui.balances.bucketDetail`: `GET bucket/{id}` and `GET accumulatedBalance/{id}`). The action tabs
list `topupBalance`, `adjustBalance`, `reserveBalance`, `transferBalance` (a transfer's receiver is
named through `GET bucket/{receiverBucket}`); a row opens the kind's GET in the Balance action
dialog of the subscriber card (`ui.subscriber.prepaid.historyDetail`), with its actions. Postpaid
accounts have no bucket and never appear. Design: [next-slices-design.md](next-slices-design.md)
section 22.

### Integrations (`ui.integrations`, `ui.integrations.list|new|form|events|delete|confirmDelete`) and System (`ui.system`) - `screens/Integrations.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: delivery and health StatusPill use their actual states.

Route `/integrations`, sidebar System > Integrations, visible to every role. Lists the live hubs
from `GET /internal/v1/eventSubscription` (API, callback, events, subscribed, last delivery,
failed count). New subscription: API (all ten), callback URL, event checkboxes of that API
(`api/hubs.ts`, checked against the server's list); it POSTs `{callback, query?}` to the API's
`/hub`. Delete confirms, then DELETEs the hub's own path; a refusal (422 for a callback the server
will not use) shows in the dialog. The System panel (`ui.system`, `ui.system.health|billingStatus|schema`)
reads `/healthz` and `/billing-status` (a 503 body is shown) and links the BssBillingAccount schema
in a new tab. Design: [next-slices-design.md](next-slices-design.md) section 23.

### Billing runs (`ui.billingRuns.list`, `ui.billingRuns.catchUp`, `ui.billingRuns.retry`) - `screens/BillingRuns.tsx`

Ledger v2 slice 3b2b2b2: inline information, warning and validation messages use shared Notes with their existing text and markers.

Ledger v2: run/runOutcome StatusPill; progress width uses a runtime CSS variable.

Stage 4, slice g, from [archive/stage4-design.md](archive/stage4-design.md) section 7. **The worker alone
bills; this screen reads runs and queues requests** for it.

- **Title row**: "Scheduled daily at `BILLING_RUN_AT` UTC+3" (from the context), and
  **Catch up to** a date (default and maximum: the due date - today from
  `BILLING_RUN_AT` on, else yesterday, Q5) with the **Catch up** button. The toast names
  the dates the worker will run, in order ("Catch-up queued: 3 dates, 2026-09-28 –
  2026-09-30"), or says nothing was due.
- **Gap warning** (Q4): when the latest run is older than the day before the due date, a
  note says the worker will not bill the gap on its own and which dates a catch-up bills.
- **Failure banner** (latest run failed): why - N of M accounts not billed, or the run
  itself failed (no later date until it succeeds) - and **Retry run for DATE**.
- **Latest run** panel: date, status, trigger (catch-up / retry) and attempt tags, short
  run id, Start / End / Duration (times in BILLING_TZ) / Charges / Amount in the UI
  currency; a progress bar ("N of M accounts") while running; the error when failed;
  **Accounts** opens the run.
- **Run history** (`GET /billingRun?limit=14`, newest first): run date, start, end,
  duration, status, note (latest / trigger / attempt tags, the error, a **Retry** link on
  **every** failed row - any failed run can be retried, Q3), charges, amount; footer
  "Showing 14 of N". A row opens the run.
- **One run** (dialog): started / finished / last progress, account counts, charges per
  currency, the error, and its per-account outcomes filtered by Failed / Blocked / OK /
  All (the first non-empty of failed, blocked), each account linking to its card, with
  the error or the earlier run a blocked account waits for; Retry when failed.
- **Polling**: every 5 s while a run is running, or while a request queued from this
  screen has not been carried out - i.e. until a run started at or after the request's
  `requestedAt` (server clock) has finished. After 3 minutes it stops waiting and says the
  worker has not picked the request up (locally nothing runs the worker on its own: `make
  worker-once`). A failed refresh keeps the data, stops polling and offers Retry. While
  waiting, Catch up and Retry are disabled; a refusal (409 a retry already queued, 422 a
  date not due yet) shows the server's message.

API: `GET /internal/v1/billingRun?limit=14`, `GET .../billingRun/{id}`,
`GET .../billingRun/{id}/accounts?status=&limit=100`, `POST .../billingRun/catchUp`
(`{"upTo": "YYYY-MM-DD"}`), `POST .../billingRun/{id}/retry` (no body).

## Differences from the design handoff

| Design | Built | Why |
|---|---|---|
| Subscriber = customer; status Active / Inactive | a billing account; the account's real state Active / Suspended / Closed | the account carries balance, cycle day and products, and ordering checks its state |
| "Registered" column | "Cycle day" | TMF629/666 have no creation date; `created_at` is the database's clock |
| Weekly recurrence | monthly only | billing supports monthly only |
| "Charged on confirmation" | charged by the run of max(start, tomorrow) | end-of-day rule of the billing run |
| Deactivate = PATCH product status | a TMF622 delete order | orders are the only way to end products |
| Proration in the browser | the backend's dry run (`firstCharge`) | the UI never computes a charge |
| Billing runs: "Run manually" for a cycle date | **Catch up to** a date: the worker runs every missing date up to it, in order | the stage 4 note wins over the handoff (Q11): a date is never billed out of order |
| Billing runs: "Re-run" (latest run) | **Retry**, on every failed run (banner, row, run dialog) | Q3: any failed run can be retried |
| Billing runs: "Scheduled daily at 02:00", "Cycle date", "last 14 days" | `BILLING_RUN_AT` (00:30) from the context, "Run date", "last 14 runs" | the run time is configuration; the list is the 14 newest runs |
| Billing runs: "manual" tag | "catch-up" / "retry" trigger tags and "attempt N" | the API reports the trigger and the attempt |
| Billing runs: per-account detail | a run dialog with failed / blocked / ok accounts | stage 4: isolation is per account |
| Statuses the design lacks (`created`, `pendingTerminate`, Suspended, Closed, ...) | explicit D4 families with shape and label | Ledger v2 |

## Tests

Subscriber numbers: `/numbers` provides inventory search, creation, state details,
assignment links, lock/unlock and Delete number (`ui.numbers.delete`, a number never assigned; the
server refuses one with history). Add product requires a number when the selected
offering's specification requires `msisdn`. New subscriber optionally continues to
this product step after creating the account. A product's Subscriber number dialog
shows the effective number and submits changes through a modify order. The picker
shows currently available unlocked stock; the server checks dates and quarantine.

`make web-test`: vitest, run twice - in UTC and with `TZ=America/New_York` - so every
request body is proven independent of the browser's zone. `make web-lint`: tsc, eslint,
prettier. `make web-e2e`: Playwright against the built UI and the seeded local stack,
failing on any console error or 5xx (`frontend/e2e/smoke.spec.ts`; it also
opens Billing runs and one run). Billing data is read-only except the prepaid test, which
tops up and orders for Pavel Orlov on the disposable seeded stack.

- **Every request a component test makes must be stubbed** (`src/test/fetch.ts`): an
  unmatched one rejects (the UI shows a network error) *and* fails the test after it ends
  (`src/test/setup.ts`). Before, a missing stub only rendered an error, and whether that
  rendered before an assertion decided the test - the cause of the flaky App.test
  "recovers on Retry" (2 of 5 runs on master). `pending` stubs a load whose answer the
  test does not care about.
- **`expectUi(id)`** (`src/test/expectUi.ts`) asserts the element with `data-ui="<id>"` is
  on screen and records the id on the test; `make progress` runs vitest with
  `scripts/ui-tested-reporter.mjs`, which lists the ids of the tests that **passed**, and a
  ui item is "tested" when its id is among them. An id that is not a ui item in
  `scope.yaml` fails `make progress`.
- **Polling tests** fake only `setTimeout` (`vi.useFakeTimers({ toFake: ['setTimeout',
  'clearTimeout'] })`) and wait with their own `until` helper: Testing Library's
  `findBy`/`waitFor` hang under a fake `setTimeout` (their wrapper drains with a
  `setTimeout(0)`).

Ledger v2 slice 1 applies the warm-grey palette and nine-step type scale through shared
tokens and temporary aliases to every screen. `make web-shots` captures read-only seeded
pages at 1280 and 1600 px in `frontend/.shots/`; layout changes follow in later slices.

Ledger v2 slice 3a applies shared 36/32/28 px table rows, sticky headers, keyboard row activation and selection, and left-aligned list states. Skeleton bars appear after 200 ms while retaining row height. Query-aware no-results states list the search and active filters.

Ledger v2 slice 3b2b2a adds optional actions and a six-second lifetime to the shared
bottom-left success toast. UI kit previews the toast and provides a live Show toast control.

Ledger v2 slice 3b2a adds stable failure times in the business time zone to shared list
errors (bootstrap errors omit the time while context is unavailable). UI kit previews
info, warning and negative Notes, including bold verdicts and mono server codes.

Shared Ledger foundations: every existing PageTitle now uses PageHeader title/count typography; Panel headers use the 44 px Components geometry. Screen requests and actions are unchanged.

Ledger v2 slice 3b2b2b1 applies shared S/M/L Dialog geometry to every Modal caller,
including context, close control, body spacing and footer. Legacy widths map at 440/620
to 400/560/760 px. UI kit previews all sizes and the step header; D7 write-state changes
are completed in slice 3b2b2b2.

Ledger v2 slice 3b2b2b2 migrates all legacy note callers, including nested catalog forms, product actions, subscriber forms and prepaid write/watch panels. Shared Footer shows negative Refused notes and warning unsure notes with the failed call; unsure replaces the write action with primary Refresh. Busy disables Cancel and shows the primary spinner, and shared-footer editable forms disable their fields. UI kit previews all four write states. The current ApiError exposes no server code, so live refusals omit it; Footer supports an optional presentation code, shown in the gallery and component test.
