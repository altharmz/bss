# UI redesign release evidence (GUI review fixes + Ledger v2)

Release record for epic #43, written by R16 (#64). It covers the GUI review fixes (G
slices) and the Ledger v2 redesign (R slices), as built on `master`. The release checks
for the deployed stand are #8: they fill the **Result on the stand** column below after
the human deploys.

## 1. Build

| Check | Result |
|---|---|
| Commit | `7f93bfa` (R16 code commit on branch `issue-64-r16-visual-qa-e2e-release-evidence-extended`, on top of `master` `605bd83`) |
| `make web-test`, UTC | 50 files, 832 tests passed |
| `make web-test`, America/New_York | 50 files, 832 tests passed |
| Backend (pre-commit hook) | 2123 passed (`pytest -n auto`, full suite, in the hook of `7f93bfa`); harness scripts: 15 suites, billing-status check 17 cases, pre-commit lock 4 cases |
| `make web-e2e` | 9 passed, 0 failed, 0 skipped (`dialogs.spec.ts` ×2, `numbered-transfer.spec.ts`, `smoke.spec.ts` ×6) |
| `make web-shots` | passed; 169 PNGs in `frontend/.shots/` (not committed) |

e2e changes, all selector or fixture changes that follow the design (each has a
`TEST-CHANGE:` line in the commit): "Financial accounts" (G7), the Top up confirm by its
outcome label `/^Top up/` (R11), "Cash" (G14), the merged name/code catalog cell (R9),
the paged "Showing a–b of n" history footer (G6), a double click at 1920 px where a click
only selects (R15), and a free subscriber number for the numbered seed offering (G15). No
assertion about a request, state, refusal or value was weakened.

## 2. Shots reviewed

Each artboard was rendered from `design-ref/design_handoff_bss_admin_v2/artboards/` in
the same Playwright image (Google Fonts unavailable there, so type is compared for size,
weight and colour, not glyph shape) and compared with the shot at the same width. Pages
without an artboard were checked against D1-D13 and the Components/States artboards; dark
shots only for legibility and token use (the handoff has no dark artboards). Every shot
passes the script's own check that nothing scrolls horizontally.

| page | 1280 | 1600 | 1280 dark | 1600 dark | artboard | result |
|---|---|---|---|---|---|---|
| balance-total-detail | ✓ | ✓ | — | — | — | OK |
| balances | ✓ | ✓ | ✓ | ✓ | — | OK |
| balances-totals | ✓ | ✓ | — | — | — | OK |
| billing-runs | ✓ | ✓ | ✓ | ✓ | BillingRuns, BillingRuns-1600 | OK |
| billing-runs-outcomes | ✓ | ✓ | — | — | BillingRuns, BillingRuns-1600 | fixed: account names mono blue → sans 500 ink-1 |
| billing-settings | ✓ | ✓ | ✓ | ✓ | — | OK |
| bills | ✓ | ✓ | ✓ | ✓ | — | fixed: Kind wrapped; long subscriber cells clip; raw state filter → #178 |
| card-postpaid | ✓ | ✓ | ✓ | ✓ | SubscriberCard, SubscriberCard-1600 | fixed: Next charge caption ran into Price |
| card-postpaid-account | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-postpaid-bills | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-postpaid-charges | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | fixed: Date touched Period (92 → 108 px) |
| card-postpaid-menu | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-postpaid-payments | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-postpaid-transactions | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | fixed: Date touched Type (92 → 108 px) |
| card-prepaid | ✓ | ✓ | ✓ | ✓ | SubscriberCard, SubscriberCard-1600 | fixed: Next charge caption ran into Price; rail shows raw `cash` → #178 |
| card-prepaid-account | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-balance | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-balance-actions | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-bills | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-charges | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | fixed: Date touched Period (92 → 108 px) |
| card-prepaid-commands | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-menu | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-payments | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | OK |
| card-prepaid-transactions | ✓ | ✓ | — | — | SubscriberCard, SubscriberCard-1600 | fixed: Date touched Type (92 → 108 px) |
| catalog | ✓ | ✓ | ✓ | ✓ | Catalog, Catalog-1600 | OK |
| catalog-catalogs | ✓ | ✓ | — | — | Catalog, Catalog-1600 | OK (shots now wait for the skeleton) |
| catalog-categories | ✓ | ✓ | — | — | Catalog, Catalog-1600 | OK |
| catalog-detail | ✓ | ✓ | — | — | Catalog, Catalog-1600 | fixed: inspector Prices columns overlapped (1600); footer below the fold → #178 |
| catalog-detail-menu | ✓ | ✓ | — | — | Catalog, Catalog-1600 | OK |
| catalog-import-export | ✓ | ✓ | — | — | Catalog, Catalog-1600 | OK |
| catalog-specifications | ✓ | ✓ | — | — | Catalog, Catalog-1600 | OK |
| dialog-add | ✓ | ✓ | — | — | AddProduct | fixed: long offering name ran into the price |
| dialog-add-prepaid | ✓ | ✓ | — | — | AddProduct | fixed: long offering name ran into the price |
| dialog-category | ✓ | ✓ | — | — | — | fixed: Offerings checkboxes stacked and centred |
| dialog-edit | ✓ | ✓ | — | — | — | OK |
| dialog-financial-accounts | ✓ | ✓ | — | — | — | OK |
| dialog-money-adjust | ✓ | ✓ | — | — | — | OK |
| dialog-money-hold | ✓ | ✓ | — | — | — | OK |
| dialog-money-top-up | ✓ | ✓ | — | — | — | OK |
| dialog-new-subscriber | ✓ | ✓ | — | — | — | fixed: checkbox stacked over its label |
| dialog-offering | ✓ | ✓ | — | — | — | fixed: Sellable checkbox stacked |
| dialog-order | ✓ | ✓ | — | — | — | OK |
| dialog-order-details | ✓ | ✓ | — | — | — | OK |
| dialog-payment-detail | ✓ | ✓ | — | — | — | OK |
| dialog-product-number-card-postpaid | ✓ | ✓ | — | — | — | fixed: body had no padding; stuck Loading number → #178 |
| dialog-product-number-card-prepaid | ✓ | ✓ | — | — | — | fixed: body had no padding; stuck Loading number → #178 |
| dialog-record-payment | ✓ | ✓ | — | — | Components, States | OK |
| dialog-record-refund | ✓ | ✓ | — | — | — | OK |
| dialog-refused-order | ✓ | ✓ | — | — | — | fixed: body had no padding |
| dialog-specification | ✓ | ✓ | — | — | — | fixed: checkboxes stacked |
| dialog-transfer | ✓ | ✓ | — | — | TransferBalance | OK |
| dialog-transfer-amount | ✓ | ✓ | — | — | TransferBalance | OK |
| dialog-transfer-confirm | ✓ | ✓ | — | — | TransferBalance | OK |
| dialog-transfer-recipient | ✓ | ✓ | — | — | TransferBalance | OK |
| dialog-users | ✓ | ✓ | — | — | — | OK |
| financial-accounts | ✓ | ✓ | ✓ | ✓ | — | OK |
| integrations | ✓ | ✓ | ✓ | ✓ | — | OK; raw System keys → #178 |
| numbers | ✓ | ✓ | ✓ | ✓ | — | OK |
| orders | ✓ | ✓ | ✓ | ✓ | — | OK; raw state filter → #178 |
| parties | ✓ | ✓ | ✓ | ✓ | — | OK |
| payments | ✓ | ✓ | ✓ | ✓ | — | fixed: long subscriber cells ran into Date |
| prepaid-commands | ✓ | ✓ | ✓ | ✓ | — | fixed: long account cells ran into State/Due |
| products | ✓ | ✓ | ✓ | ✓ | — | OK |
| subscribers | ✓ | ✓ | ✓ | ✓ | Subscribers, Subscribers-1600 | OK |
| subscribers-inspector | — | ✓ | — | — | Subscribers-1600 (wide) | OK (new shot) |
| ui-kit | ✓ | ✓ | ✓ | ✓ | Components, States | OK |
| users | ✓ | ✓ | ✓ | ✓ | — | OK |

169 files, 67 pages.

Findings that are not CSS (behaviour or wording) are tracked in #178.

## 3. D11 deviations as built

| Artboard | Built as (D11) | Built as checked |
|---|---|---|
| Subscribers "Export CSV" | Not built. | ✓ no export action on Subscribers |
| Subscribers row `⋯` menu | Not built: the whole row opens the account, as now. | ✓ no row menu; below 1536 px a click opens the card, from 1536 px a click selects (inspector) and Enter or a double click opens it (R15) |
| Card top-bar avatar and Account menu | Not built. The operator menu stays in the sidebar footer (D6). | ✓ top bar has breadcrumbs and the business-date chip only |
| Card "Customer since …" | Not shown. | ✓ not on the card (the `/ui-kit` KeyValue sample still uses the label as sample data) |
| Card Products tab, "Show 1 terminated" | Not built: current products on Overview. | ✓ |
| Card Orders tab | Not built; the meta line keeps the "Orders" link. | ✓ `ui.subscriber.orders` link to `/orders?account=<id>` |
| Catalog "In use" column | Not built. | ✓ |
| Catalog "Created … by a.lepp" | Not shown. "Updated" shows `lastUpdate` when present, else "—". | ✓ "Updated" is "—": the catalog read carries no `lastUpdate` (R9 deviation) |
| Billing runs per-row Retry | Not built. Retry is per run. | ✓ "Retry N failed" / "Retry run" in the header only |
| Billing runs reason summary | "N failed · M waiting", plus `run.error` when `failureKind === 'run'`. | ✓ |
| Add product "Reserved for you until …" | Not shown. | ✓ |
| Unsure "req 7f3a91c2" | The failed call text instead (D7). | ✓ (States gallery and Record payment) |
| Transfer step 4 inside the dialog | Not built: 3 steps, the card's "In progress" box follows the command. | ✓ Recipient, Amount, Confirm ("Step n of 3") |
| Order steps with timestamps | Command phases, not TMF622 timestamps. | ✓ |
| Display ids like `ORD-26-10-00418` | `ORD-` + the 8-hex short id (D5). | ✓ `Id` prefixes BA-, ORD-, BL-, CMD-, PAY-, RF-, PRD-, BAL- |

## 4. Deviations recorded in the slice lines

From the "Ledger v2" and "GUI review fixes" lines in
[docs/decisions/frontend.md](decisions/frontend.md):

| Slice | Deviation |
|---|---|
| R16 (#64) | The card's Charges and Transactions Date columns widen from 92 to 108 px in their TSX column lists (a grid track is set there, not in CSS). |
| R15 (#62) | Subscribers inspector Products line is "N active" without offering names (a wider field list would change a pinned request); Catalog inspector lifecycle actions stay in the footer, not the header. |
| R14 (#60) | The `--fs-*` aliases became `--size-*` tokens in `tokens.css`; the Theme control is a pressed-button group, not radios; the shots script dropped two seed-dependent assertions. |
| R11 (#52) | Prepaid money actions and transfer keep their safe idempotent Retry after an uncertain result instead of the generic Refresh. |
| R9 (#48) | `CatalogOffering` has no `lastUpdate` or lifecycle dates: "Updated" is "—" and stepper captions show sellability, not "since" dates. |
| R8 (#46) | Outcomes keep the existing lazy reads (no automatic load); Catch up keeps its visible date input instead of a new dialog. |
| Slice 7b | `AccountProduct` exposes no number value: the lazy Subscriber number action stays; Next charge stays as a Since caption. |
| Slice 7a | Transactions carry business dates, not timestamps; no subscriber-number field. |
| Slice 6b2 | Accepted-order/transfer watches stayed above Card until slice 7 placed the In progress box (resolved in 7b). |
| Slice 4 | Session and Context have no display name: the footer shows the login and its first two characters. |
| Slice 3b2b2b2 | `ApiError` lacks a server code, so live refusals omit it; the gallery uses one column at 1280. |
| Slice 3b2b1 | The money strip wraps when the viewport cannot fit the artboard's equation and actions (no horizontal scroll). |
| Slice 3b2a | The bootstrap `ErrorBlock` has no system context, so it omits its timestamp. |
| G5 (#45) | No pending-order Activate action exists, so there is no third Activate caller to gate. |
| G4b (#23) | TMF620's discriminator omits `BooleanCharacteristicValueSpecification`; predefined boolean values are disabled with an explanation. |

Slices 1, 2b1, 3b1, 3b2b2a, 5 and 6b1 record no deviation; the other G lines record
none.

## 5. Release checks P1-P16 (#8)

"Result on the stand" stays empty until #8 runs after the human deploys.

| Gap | What | Slice | Built | Result on the stand |
|---|---|---|---|---|
| P1 | Spec "Requires a subscriber number" (numbers end to end, with B3) | G4b (#23) | built | |
| P2 | Parties: New individual / New organization | G10 (#55) | built | |
| P3 | Party edit: status, contact media, birth date, identification, external reference (with I9) | G10 (#55) | built | |
| P4 | Party dialog: New customer | G10 (#55) | built | |
| P5 | Add billing account (customer dialog, card More actions) | G11 (#56) | built | |
| P6 | Customer's billing accounts with Delete; Delete customer when none remain | G11 (#56) | built | |
| P7 | New offering: description, isSellable, categories | G7 (#49) | built | |
| P8 | Offering detail: Add price | G7 (#49) | built | |
| P9 | Delete offering reachable (In design ⋯ menu) | G1b, G7 (#49) | built | |
| P10 | Delete category / catalog reachable | G1b, G7 (#49) | built | |
| P11 | Spec characteristics editor | G4b (#23) | built | |
| P12 | Top up: optional Number (`logicalResource`) | G9 (#53) | built (number); **voucher not built**: no backend voucher management | |
| P13 | Numbered transfer by subscriber number | G4b (#23), verified in R16 (`numbered-transfer.spec.ts` passed) | built | |
| P14 | Balances Account totals; card accumulated balance KeyValue | G13 (#59) | built | |
| P15 | Record refund from the Payments tab and the payment dialog | G9 (#53) | built | |
| P16 | Record payment: External reference (`correlatorId`) and description (with I7) | G9 (#53) | built (reference, description); **value date not built**: `paymentDate` is read-only | |
