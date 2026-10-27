# GUI review fixes merged with the Ledger v2 redesign: plan and tracker (2026-10-27)

Source: the GUI test of the deployed stand (`https://bss2811.duckdns.org:8445/`, HEAD
`ebf779d`, business date 2026-10-27, USD, 1920×903) reported bugs B1-B28, MCP/GUI
parity gaps P1-P16, improvements I1-I13 and data-quality items D1-D5. This file is
the single plan for every pending task: those findings, the remaining redesign slices
(R7b-R16 from [ui-redesign-slices.md](ui-redesign-slices.md)) and the remaining backlog.
**The human delegated every decision on 2026-10-27; none is left open, and no slice needs
approval.** The human only starts the run and deploys.

Contents: [Verified findings](#verified-findings) · [Decisions](#decisions) ·
[Tracker](#tracker) · [Orchestrator](#orchestrator) · [Slice specs](#slice-specs) ·
[Data quality](#data-quality-d1-d5-scripts-only) · [Track C](#track-c-needs-the-human) ·
[Release checks](#release-checks)

---

## Verified findings

Every finding was checked against the code at `ebf779d` (the deployed commit). The
review was right about the symptoms. Several root causes differ from its guesses, and
these change the work:

| ID | What the code shows |
|---|---|
| B1 | The product-change dialogs already read `state` and set a "Rejected: …" problem (`screens/ProductActions.tsx` `submit`). They render it as a `Note` **inside the footer action row**, unlike the D7 refused state built in slice 3b2b2b2 (a Note above the footer). The `openModifyItem` reason never reaches the operator. Likely causes: the footer squeezes the Note, or the dialog remounts on the card reload (the same mechanism as B6). G2 confirms which one with a test first. `pendingChanges` lists only status changes dated **after** today (`app/repository/products.py` `pending_changes`), so a same-day suspension has no Undo. |
| B2 | `Dialog` (`ui/kit.tsx`) has no max-height and no scrolling body. Every tall dialog overflows the viewport. Two "missing" functions were really below the fold: **Delete billing account** in Edit subscriber (`SubscriberForms.tsx`) and **Delete category/catalog** in their edit dialogs (`Classification.tsx`). |
| B3 | The number picker exists in Add product and Edit draft (`AddProduct.tsx`, via `requiresNumber`). *Change number* exists too (`ProductNumber.tsx`). Both show only when the product specification references the `msisdn` resource specification. No spec on the stand does, and the local seed's "Mobile Voice & Data" doesn't either. The backend fixes `product.requires_msisdn` when the product is added (`app/ordering/numbers.py`). It refuses a number on a product that does not require one (`numberNotRequired`). It refuses an offering change combined with a number change ("change the offering and the number in separate orders"). It refuses to change a spec's msisdn requirement once the spec has been ordered (`app/api/catalog.py`). The UI cannot set the requirement at all, because the spec editor has no field for it. |
| B5 | The literal `'Balance EUR'` in `screens/Subscribers.tsx`. Its source is the redesign plan: slices 7, 9, 10 and 11 of [ui-redesign-design.md](ui-redesign-design.md) write "EUR" literally. Fixed at the source by D13 there. |
| B7 | `GET /internal/v1/billingRun` already pages (`offset`/`limit`, newest first). The screen reads only the first page. |
| B8 | `_next_charge` (`app/internal/accounts.py`) returns the next cycle boundary after today. It ignores that the first prorated period is charged by the next daily run. |
| P6, P9, P10 | The calls exist (`deleteBillingAccount`, `deleteCustomer`, `deleteOffering`, `deleteCategory`/`deleteCatalog`). They are unreachable (B2) or hard to find. They become reachable through G1, and G7/G11 verify them. |
| P12 | The backend refuses `voucher` ("no voucher management", `app/api/topup.py`). |
| P14 | `accumulatedBalance` is read but only merged into bucket rows (`api/balances.ts`). It is never shown as its own record. |
| P16 | TMF676 `paymentDate` is read-only in our API (`app/api/payment.py`). `correlatorId` is an accepted document. |
| Parity check | `tests/test_ui_mcp_parity.py` is per operation. It passed because every P row's operation is called somewhere. The review's gaps are at the attribute and placement level, so this plan tracks them by ID. The check stays per operation. |

The review's "~840 autotests" is stale. The hook runs 1,495 backend tests and 284
frontend tests in each timezone.

## Decisions

### Process

1. **Merged order:** finish the shared-component defects first (G1-G4). After that,
   each redesign slice is followed by the behaviour fixes for the same screens, so
   behaviour work lands on restyled code and no screen is touched twice in a row by
   unrelated work.
2. **Redesign slices keep their rules** ("visual and layout only", ui-redesign-design.md),
   and gain D13 (currency). **G slices may change requests and the backend**, within the
   limits written in their spec.
3. **No migration in this plan.** A slice that finds one necessary stops and reports.
   TMF622 gains one new refusal code (G4). It is carried in the existing 201 `rejected` +
   `productOrderErrorMessage` shape, so the contract does not change. All other backend
   changes are to the internal API.
4. **Commits:** one commit per G or R row, through the hook. The subject is imperative in
   the repo's style and ends with the IDs, e.g. "Show refused product orders in their
   dialog (B1, I4, I5)". This replaces the review's `fix(gui):` prefix.
5. **Deploying stays the human's decision** (CLAUDE.md). The review's "deploy and smoke
   test" step becomes [Release checks](#release-checks): written by R16, run after the
   human deploys (`/ship`).
6. **Phase 4 (improvements):** no confirmation step, because the decisions are below.
   Included: I1-I13, each in a slice. I2 is included in a reduced form (see G4).
7. **Staging data is never changed by a session.** D1, D3, D4 and D5 become dry-run-first
   scripts the human runs. D2 is a written decision with no script.

### By finding

| ID | Decision |
|---|---|
| B1 + I4 | A new module `api/orderErrors.ts` maps every `productOrderErrorMessage.code` the backend emits to an operator sentence, and falls back to `reason`. Every TMF622 POST/submit caller uses it: Add product, Edit draft submit, product change, Change offering, Change number, Deactivate/End and the Orders draft submit. A `rejected` result shows the D7 refused state (the Note above the footer, with the code in mono) and keeps the dialog open with its inputs. |
| B1 Undo | Internal `AccountProduct` gains `undoableStatusChange`: `{date, status, productOrder: {id}}` or null. It is the status change dated today that is already applied and whose order `cancelProductOrder` still accepts. The row menu offers **Undo suspension** / **Undo resumption** in place of the opposite action that day. It cancels through the existing cancel call, like the offering-change Undo. |
| I5 | A future-dated suspension already shows as a pending change with Undo. G2 adds the missing test only. |
| B2 | `Dialog` gets a `max-height: calc(100dvh - 2 × --space-8)` column layout: a fixed header, a scrolling body (`overflow: auto`, `min-height: 0`) and a fixed footer. The overlay never scrolls. Enter in a single-line input submits through the footer primary when it is enabled. It does not submit from a textarea, on the transfer confirm step (D7), or from a destructive primary. |
| B3 / P1 | **No new API.** Numbers belong to products whose specification requires them, and that stays the rule (it is the backend design: claims, quarantine, release on end). The GUI gains what was missing: the spec editor's **Requires a subscriber number** checkbox (G4). It becomes read-only with the server's 409 text once the spec has been ordered. The existing pickers and *Change number* then appear. *Assign number* to a product whose spec does not require one is **refused by design** (`numberNotRequired`). Products already sold without a number stay without one. On the stand, numbered products come from a numbered spec (D4). |
| I2 | *Change offering* lists only offerings with the same product specification as the product's current offering. The API keeps accepting any offering, except that a change whose target spec's msisdn requirement differs from `product.requires_msisdn` is rejected with the new code `numberRequirementDiffers`. That check keeps the number claims consistent. Linking the stand's existing Mobile spec is impossible, because it has been ordered; D4 creates a numbered spec instead. |
| B4 | `Menu` renders its item list in a portal on `document.body`, positioned under its trigger with `position: fixed`. It flips up when the space below is smaller than the list. It closes on scroll and resize. This applies to every Menu. |
| B5 | Subscribers: the header reads "Balance" plus the currency when all loaded rows share one, otherwise "Balance". Each amount shows its own currency through `Amount`. No currency literal anywhere in `src/` outside tests and `UiKit.tsx` samples. A style-guard check enforces that (`/\b(EUR\|RUB\|USD)\b/` in a non-test `.tsx` string literal fails). |
| B6 | Dialogs opened from the subscriber card are owned by a card-level state keyed by stable ids (account id, product id, action id), not by list rows. A refetch never unmounts them. Polling and list refetches pause while a dialog has a write in flight (a `WriteLock` context with a counter). A confirm that resolves without sending its request shows "Not sent. Try again." |
| B7 | Billing runs history pages with the Orders pager (`ORDER_PAGE`-style offset pages of 20) on the existing endpoint. No date filter. |
| B8 | Internal `nextCharge` becomes the business date of the first billing run that will write a charge for the product. It is computed with the worker's own due rule (billing-core.md "When a period is charged"), not with the cycle boundary. A product starting after today shows "Starts <date>" instead of "Activated". |
| B9 | The Add product preview shows "Balance after this charge" = account balance − unbilled charges (the card's loaded `charges`, passed in) − this charge. The label becomes "Balance after unbilled charges and this one". |
| B10 | Writes that change a sidebar count (subscriber create/delete, any order POST, offering create/delete) bump the shell's `version` that the count resources are keyed by. |
| B11 | The shared money validation checks the sign before the decimals: "Amount must be greater than 0". |
| B12 | `Checkbox` renders as `<label class="bss-check"><input/> text</label>`. The event-type list becomes a full-width wrapped checklist with no horizontal scroll. |
| B13 | Order items read "Suspend <product name>", "Resume <product name>", "Change <product> to <offering>", "Change number of <product>", "Add <offering>", "End <product>". Names come from the item when present, else from one TMF637 GET per product, done only in the order dialog. The order dialog shows the target status. |
| B14 | Bills: "Amount due" becomes "Total"; "Remaining" stays. |
| B15 | "Save details" keeps the order dialog open, reloads the order and shows "Details saved" in the footer state slot. |
| B16, B17 | Name cells ellipsize with the full text in `title`. Ids, dates and periods are `white-space: nowrap` (in the `Id` primitive and the date/period cells). |
| B18 | *Deactivate* opens the *End on a date* dialog with today prefilled (size S, same request as today's Deactivate when the date is today). The inline confirm goes away. |
| B19 | The toast action uses `--color-primary-tint` on ink-1 (D8). The contrast is fixed in the token if it is below 4.5:1. |
| B20 | Built-in Monthly "Used by" = the number of loaded billing accounts with no `billingCycleSpecification` reference. |
| B21 | Offering pickers show the price valid at midnight of the selected start date (the existing `shownPrice` with that date). |
| B22 | Edit draft scrolls the selected offering row into view (`block: 'nearest'`). It is highlighted by the R10 selected style. |
| B23 | "Subscriber no." becomes "Customer no." An account can hold several numbers; the numbers show in the Products table. |
| B24 + I6 | Prepaid wording: no "billing run of …" sentence where the charge is prepaid. It says "Paid from the balance on <date>". Prepaid forms hide the billing cycle day. Every prepaid action that charges at once (Add product with an immediate start, Resume after paid-through, the pending-order Activate) is disabled when the server preview shows a shortfall, and offers "Top up <shortfall>…". This is the R10 pattern. |
| B25 + I12 + I13 | One `format/labels.ts` holds human labels for API codes: payment methods, run triggers (`onCycle` → "Scheduled", `offCycle` → "Off-cycle"), order/item/bill/command states, event types ("CustomerBill" → "Bill", "TMF654 balanceActionHistory" → "Balance action history"). `StatusPill` and every table use it, with the raw code in `title`. A `plural(n, noun)` helper fixes counts. Fixed strings: "Create as In design", a "Customers N" heading on the Customers tab, import error "Choose a file exported from a catalog export". Billing status reads "Last run 2026-10-27 · 16 h ago". |
| B26 | One form pattern: dates use `DateField` (Suspend/Resume/End/Hold included). Payment method is a `Segmented` (Bank transfer, Card, Cash, Other + name) in both Record payment and Record refund. Native selects use `SelectField`. The file input becomes a `FileField` primitive (a button plus the file name). Labels are left-aligned above the input. "New category/catalog" moves to the PageHeader actions. Non-destructive confirms (Release hold) use the primary style. The number placeholder is "12025550123". |
| B27 | Every input gets a `<label for>` or `aria-labelledby` (New offering's Code, Name and Price included). A vitest check over the gallery forms fails on an unlabeled input. The sidebar "Accounts" becomes "Financial accounts". |
| B28 | Holds show "Holds through <validFor.end − 1 day>" as a date, the day the operator entered. |
| P2 | The Parties PageHeader gains "New individual" and "New organization" (TMF632 POST: names, status, contact media, plus birthDate for individuals). |
| P3 + I9 | Party edit adds status, one email, one phone and one postal address (TMF632 `contactMedium` with `contactType` email/phone/postalAddress), birthDate (individual), one identification (`individualIdentification` / `organizationIdentification`: type + id) and `externalReference` (name + type). The backend stores these as documents. New subscriber gains the email and phone fields. |
| P4 | The party dialog gains "New customer" (TMF629 POST with `engagedParty`, name defaulting to the party name, status Active as in New subscriber). |
| P5 | "Add billing account" on the customer dialog and in the subscriber card's More actions. It uses the account step of New subscriber: mode, currency, cycle day (postpaid), credit limit (postpaid). |
| P6 | The customer dialog lists the customer's billing accounts, each with Delete (an inline confirm; the server's 409 shown in place). Delete customer is enabled once none remain. "Remove billing accounts first" links to that list. |
| P7 | New offering adds description, isSellable (default on) and categories (a multi-select from `loadCategories`). |
| P8 | The offering dialog's Prices table gains "Add price": one-time or recurring, amount and currency (the system currency), valid from a date of today or later. It sends a TMF620 price POST plus the offering's price reference PATCH, through the same calls MCP uses. Edit, End and Delete exist and stay. |
| P9, P10 | Verified reachable after G1. P9: Delete is in the offering ⋯ menu for In design (R9). |
| P11 | The spec editor adds "Requires a subscriber number" (see B3) and a characteristics editor: rows of name, value type (string / integer / boolean), description, configurable, and values (comma-separated, the first is the default). These are stored as TMF620 `productSpecCharacteristic` documents with no ordering behaviour. |
| P12 | Voucher: **not built.** The backend refuses vouchers (no voucher management) and adding it is out of scope. Number: Top up gains an optional "Number" select of the account's active numbers (TMF654 `logicalResource`). |
| P13 | Verified in G4's end-to-end scenario (a numbered transfer). |
| P14 | Balances gains an "Account totals" tab (the accumulatedBalance list, with detail by GET). The prepaid card's Balance tab shows the account's accumulated balance in a KeyValue. |
| P15 | "Record refund" also appears in the subscriber Payments tab header and in the payment dialog. It opens the same dialog with the same body. A payment-specific refund stays out, as in the refund slice decision. |
| P16 + I7 | Record payment gains "External reference" (TMF676 `correlatorId`) and a description. **No value date**: `paymentDate` is read-only, so a payment is dated by the business clock. Adjustments and refunds make the reason/description field mandatory (UI validation only). |
| Retry | BillingRuns and PrepaidCommands get component tests with a failed fixture that assert the Retry action and its request (G6). |
| I1 | New subscriber stays three TMF POSTs (TMF defines no composite operation, so the follow-TMF rule applies). It compensates on failure: if the customer POST fails, it DELETEs the party; if the account POST fails, it DELETEs the customer and then the party. If a compensation fails, the error lists the created ids with links to Parties. The tests pin the DELETE sequence. |
| I3 | Catalog rows of offerings with `isSellable=false` get a "Not sellable" `Tag`, in every lifecycle group. |
| I8 | A new `Combobox` primitive (`ui/combobox.tsx`, ARIA combobox + listbox, filter as you type, keyboard, no new dependency). It is used for the party picker in New financial account, the offering pickers in Add product and Change offering, and the number pickers. |
| I10 | The subscriber card shows the account's draft and in-progress orders in the Overview, above Products, with an Open link. Drafts show the item price (the offering's price at the requested start). |
| I11 | Orders shows newest first without an API change. It reads `X-Total-Count`, then requests the last page and walks backwards. After a write, only the changed row is refetched; a skeleton shows only on the first load. |
| billCycleDay | Not in this plan. PATCH stays a 409, and the open question in billing-core.md stays as it is. |
| Demo data in prod | The deployed instance is a demo stand. It keeps the data the operators create there. The D scripts are the only way to change it, and the human runs them. The "Demo data in prod" item in status.md closes with this decision. |

---

## Tracker

Status: `todo` · `done: <commit subject>` · `split`. R rows are specified in
[ui-redesign-design.md](ui-redesign-design.md) ("Slice N"). G rows are specified below.
Rows run strictly in order.

| # | Slice | Kind | Done when | Status |
|---|---|---|---|---|
| G1 | Dialogs fit the viewport, menus escape their card, no hard-coded currency (B2, B4, B5) | UI | Split by ID: G1a then G1b | split |
| G1a | Menus escape their card; no hard-coded currency (B4, B5) | UI | Portal/flip-up/dismissal regression tests; every single-row menu item present; subscriber single/mixed currencies; currency guard | done: Keep menus visible and show account currencies (B4, B5) |
| G1b | Dialogs fit the viewport and submit from single-line inputs (B2) | UI | Fixed header/footer with scrolling body inherited by Modal; optional onSubmit wired to every form primary, with disabled/textarea/destructive/transfer-confirm exclusions; structure and Enter tests; new frontend/e2e/dialogs.spec.ts at 1366×768 and 1920×900 checks New category, Edit subscriber with media, New offering and Add product primaries with toBeInViewport(); run make web-e2e once; screenshots and visual review | done: Keep dialog actions visible and submit enabled forms with Enter (B2) |
| G2 | Refused orders shown in their dialog; same-day Undo (B1, I4, I5) | internal API + UI | A 201 `rejected` keeps every order dialog open with the mapped sentence and code. Undo suspension cancels a same-day suspension. Future-suspension Undo is tested | todo |
| G3 | Writes survive refreshes; sidebar counts update (B6, B10) | UI | A refetch between "Release hold" and "Yes" still sends the PATCH. A create bumps the counts | todo |
| G4 | Subscriber numbers end to end (B3/P1, P11, I2, P13) | TMF622 refusal code + UI | Spec "Requires a subscriber number" and the characteristics are pinned. Change offering is filtered by spec. `numberRequirementDiffers` is tested. The e2e numbered prepaid transfer is written | todo |
| R7b | Subscriber card: products, pending sub-row, In progress steps | UI (redesign) | As in ui-redesign-slices.md row 7b, keeping G2's Undo and G3's dialog ownership | todo |
| G5 | Subscriber products and prepaid wording (B8, B23, B24, I6, I10) | internal API + UI | nextCharge = the first charging run (backend test: a product started today gives tomorrow's run). "Starts". Prepaid shortfall gating. Drafts on the card | todo |
| R8 | Billing runs: header actions, latest-run band, inline outcomes, history selection | UI (redesign) | As in row 8 | todo |
| G6 | Billing runs paging; Retry fixtures (B7, retry checks) | UI | Pages walk all 23+ runs. Retry is tested for a failed run and a failedTechnical command | todo |
| R9 | Product catalog: lifecycle groups, offering detail with stepper, other tabs | UI (redesign) | As in row 9 | todo |
| G7 | Catalog parity (P7, P8, P9, P10, I3, B21, B27 New offering) | UI | New offering's description/isSellable/categories and Add price bodies are pinned. Not sellable tag. Price at start date | todo |
| R10 | Add product dialog | UI (redesign) | As in row 10 | todo |
| G8 | Add product follow-ups and Combobox (B9, B22, I8 offerings/numbers) | UI | Balance-after includes unbilled charges. The selected draft offering is in view. Combobox keyboard/aria tests | todo |
| R11 | Money dialogs | UI (redesign) | As in row 11 | todo |
| G9 | Money dialog parity (B11, B28, P12, P15, P16, I7) | UI | Top-up `logicalResource`, payment `correlatorId`/description and the refund entry points are pinned. Mandatory reasons. Holds-through date | todo |
| R12 | Sweep 1: Products, Orders, Numbers, Parties, subscriber forms | UI (redesign) | As in row 12 | todo |
| G10 | Parties and customers (P2, P3, P4, I9, I8 party picker) | UI | New individual/organization, the party edit documents and New customer bodies are pinned | todo |
| G11 | Billing accounts and atomic New subscriber (P5, P6, I1) | UI | Add billing account pinned. Account delete from the customer dialog. The compensation DELETE sequence is pinned | todo |
| G12 | Orders (B13, B15, I11) | UI | Item labels per action. Save details stays. Newest first with the last-page read | todo |
| R13 | Sweep 2: Prepaid commands, Accounts, Bills, Payments, Balances, Billing settings | UI (redesign) | As in row 13 | todo |
| G13 | Bills, balances, billing settings (P14, B14, B20) | UI | Account totals tab and card KeyValue. "Total". Used-by count | todo |
| R14 | Sweep 3, cleanup, dark theme | UI (redesign) | As in row 14 | todo |
| G14 | Wording and consistency (B12, B16-B19, B25, B26, B27, I12, I13) | UI | labels.ts and plural() adopted. One form pattern. Label check. The guard passes | todo |
| R15 | Inspector at ≥ 1536 px | UI (redesign) | As in row 15 | todo |
| G15 | Data-quality scripts and local seed (D1-D5) | scripts + seed | `ops/staging-data/` scripts dry-run against a local stack. The seed builds a numbered mobile spec, numbers, a catalog tree and US names | todo |
| R16 | Visual QA, e2e, release evidence, extended | e2e + docs | As in row 16, plus [Release checks](#release-checks) written into the release doc and the P1-P16 table marked | todo |

## Orchestrator

Act as the orchestrator described in [ui-redesign-slices.md](ui-redesign-slices.md#orchestrator)
(before the first slice, per slice, stop conditions, never), with these changes:

- The table above is the tracker. Start at its first row that is not `done`.
- **R rows:** use that file's subagent prompt with N = the number after "R". In its step 5,
  the subagent sets the row's status **in this file**; the redesign table stays as it is.
- **G rows:** use the G subagent prompt below.
- "A backend/API change" stops an R row, as it did before. In a G row it stops the run
  only when it goes beyond what the G spec names, or when it needs a migration.

### G subagent prompt (one slice)

> You implement **slice G<n>** of docs/gui-review-plan.md in this repository, and only
> that slice.
>
> 1. Read CLAUDE.md. Then read docs/gui-review-plan.md: "Verified findings", the
>    "Decisions" rows for the IDs your slice names, "Rules for G slices" and "G<n>". From
>    docs/ui-redesign-design.md read "Rules for every slice" items 3-9 and D4, D5, D7 and
>    D13 (looks, tokens, write states). Then read docs/patterns.md. Open other code only
>    as you need it, finding it from the visible strings.
> 2. Implement exactly what the spec and the decisions say. Do not make design decisions.
>    If something is not covered, follow "When the spec is wrong" in docs/gui-review-plan.md.
> 3. Add the tests the slice lists. Each must fail without your change. Use targeted
>    runs while working (`docker compose run --rm --no-deps web-offline sh -c 'sh
>    scripts/check-deps.sh && npm run types && npx vitest run src/<path>'`; backend:
>    `docker compose run --rm app pytest tests/<file>.py -k <name>`). Node runs only in
>    containers.
> 4. Run `make web-shots` and review the touched pages at 1280 and 1600.
> 5. With an API or internal-API change, run `make docs`. Update `docs/ui.md`,
>    `docs/patterns.md` (for a primitive change), `docs/progress/scope.yaml` (new markers
>    and operations), one line under "GUI review fixes" in docs/decisions/frontend.md (and
>    in the backend decision file the slice names), and set this row's status to
>    `done: <commit subject>`.
> 6. Run `make web-fmt`, then `git add` and `git commit`. The subject is imperative and
>    ends with the IDs in parentheses. Add one `TEST-CHANGE:` line per existing test you
>    changed by design. The hook is the full verification; never use `--no-verify`. After
>    three failed hook runs, stop and report.
> 7. `git push`. Never deploy.
> 8. If the slice is too large, split it into `G<n>a`/`G<n>b` rows (by ID), commit and push
>    `a`, and report.
> 9. Report: the subject and hash, the hook's test counts, the TEST-CHANGE lines, every
>    deviation and why, and what is left.

### Rules for G slices

1. **Requests:** pin every new or changed write body in the component test **and** in
   `tests/test_ui_requests.py` (frontend conventions). Do not change an existing body
   unless the slice says so.
2. **Backend:** only the changes the slice names. TMF response shapes stay. New TMF622
   refusals use the existing rejected-order shape. No migration.
3. **Looks:** new UI uses the Ledger primitives (Dialog, Field, Segmented, Note, Table,
   Menu, StatusPill, Amount, Id). No inline styles, colour literals or px font sizes
   (style guard).
4. **Markers:** every new action, field group and list gets a `data-ui` marker, added to
   `docs/progress/scope.yaml`.
5. **Wording:** English, sentence case, human labels; no raw API codes outside `title`.
6. **TMF:** implement what the vendored spec defines and skip the rest. A field the
   backend refuses (voucher, paymentDate) is not offered.

### When the spec is wrong (G slices)

| Situation | Action |
|---|---|
| The code already does what a decision asks | Add the test that proves it, note "already present" in the commit body, continue. |
| A decision names a code or field the backend does not emit or accept | Leave that part out, record the deviation in the frontend.md line, continue. |
| A redesign-era test asserts the old behaviour this plan changes | Update it with `TEST-CHANGE:`. |
| A migration, a billing-core change or a TMF shape change looks necessary | Stop and report. |

---

## Slice specs

### G1: dialogs, menus, currency (B2, B4, B5)

- `Dialog` per decision B2 (CSS in `ui/ui.css`; the body is the scroll container;
  `Modal` inherits). Enter-to-submit: `Dialog` takes an optional `onSubmit`. The footer
  primary of every form dialog is wired to it; the exclusions are as in the decision.
- `Menu` per decision B4. Keyboard, focus return, outside click and Escape stay as tested
  in 3b1.
- B5 per decision, plus the currency guard in `ui/styleGuard.test.ts`.
- Tests: Dialog structure (the body element is the scroll container; Enter submits from an
  input, not from a textarea or a disabled primary); Menu portal (items are outside the
  card's DOM subtree, all items present for one row, flip-up when near the bottom with
  `getBoundingClientRect` mocked); the Subscribers header for one and for mixed
  currencies; the guard. A new e2e spec `frontend/e2e/dialogs.spec.ts` at 1366×768 and
  1920×900 asserts `toBeInViewport()` for the four primaries (run `make web-e2e` once).

G1 is split by ID because B2 includes migrating legacy dialogs whose footers are nested
inside loaded child components, plus wiring each form's Enter handler. G1a implements B4
and B5 only. G1b retains **all** B2 implementation and test requirements above, including
`frontend/e2e/dialogs.spec.ts`, the two viewport sizes, and one `make web-e2e` run.

### G2: refused orders and same-day Undo (B1, I4, I5)

- First, a component test reproducing B1: Suspend completed, then Resume answered with 201
  `rejected` / `openModifyItem` across a card reload. Fix the cause it shows (footer layout,
  remount, or both).
- `api/orderErrors.ts` per decision B1. List the codes from the backend
  (`app/ordering/process.py`, `numbers.py`, `prepaid.py`, `cancel.py`): every
  `(code, …)` problem tuple. The test asserts that every backend code has an entry: a
  backend test greps the codes and compares them with a JSON list the frontend exports to
  `frontend/src/api/orderErrors.codes.json`.
- Migrate every TMF622 caller to the D7 `Footer` refused state.
- Internal: `undoableStatusChange` in `open_products` (`app/internal/accounts.py`), with
  a backend test for today's suspension (present), yesterday's (absent) and a cancelled
  one (absent). Run `make docs`. Decision entry in docs/decisions/internal-api.md.
- Menu: Undo suspension / Undo resumption per decision.
- Tests: refused state per caller (one parametrized test), the Undo request, the future
  suspension's pending Undo (I5).

### G3: writes survive refreshes; counts (B6, B10)

- Per decisions B6 and B10. The `WriteLock` context lives in `api/`; `usePolled` and the
  card's reload respect it.
- Tests: release hold with a refetch injected between the click and Yes (the PATCH is sent
  once); polling paused during a write; "Not sent" when the confirm resolves without a
  request; the count resources re-request after a create.

### G4: subscriber numbers end to end (B3/P1, P11, I2, P13)

- Spec editor (`Specifications.tsx`, `api/bss.ts` `SpecificationFields`): the checkbox
  and the characteristics editor per decisions B3 and P11. Create and patch bodies are
  pinned. A 409 on the requirement shows in place.
- Backend: `numberRequirementDiffers` in `_modify_problems` for an offering change.
  Tests: target spec requires a number while the product does not, and the reverse;
  the same requirement is accepted. Decision entry in docs/decisions/tmf-api.md.
- Change offering: filter by specification; the empty-state text "No other offering uses
  this product's specification."
- Copy: ProductNumber for a product without a requirement: "This product's offering does
  not use subscriber numbers. Offerings whose specification requires a number ask for one
  when the product is added." New subscriber: "After creating the account, add a product.
  Offerings that need a subscriber number ask for one." Transfer refusal: "This account has
  no active product with a subscriber number, so it cannot send a transfer. Only products
  whose specification requires a number carry one."
- e2e scenario (written now, run in R16): a numbered spec, an offering, two prepaid
  subscribers with numbers, a top-up, a transfer by number that completes.

### G5: subscriber products and prepaid wording (B8, B23, B24, I6, I10)

- Internal `nextCharge` per decision B8, with backend tests (postpaid started today,
  started earlier mid-period, future start, prepaid). Run `make docs`. Decision entry in
  docs/decisions/internal-api.md.
- "Starts <date>" for future starts. "Customer no." (B23).
- B24/I6 per decision. Find "billing run of", "Activate" and the cycle-day field from
  their strings.
- I10: the orders in Overview, loaded with the Orders screen's list call filtered by
  account and state (`draft`, `acknowledged`, `inProgress`).
- Tests: wording for both modes, the shortfall-disabled actions with the Top up handoff,
  the drafts block with its price.

### G6: billing runs paging, Retry fixtures (B7)

- The pager under the history table (20 per page) using the existing offset/limit. The
  selection (R8) survives a page change only when the selected run is on the page.
- Tests: page 2 request string; Retry for a failed run (existing call); PrepaidCommands
  Retry for `failedTechnical` with the pinned request.

### G7: catalog parity (P7, P8, P9, P10, I3, B21, B27 for New offering)

- Per decisions. Verify P9/P10 reachability with tests that open the dialog and click
  Delete. Labels on New offering inputs.
- Tests: the pinned New offering body with the new fields; Add price (one-time and
  recurring) bodies; the Not sellable tag; the price shown at a start date after a
  scheduled price change.

### G8: Add product follow-ups and Combobox (B9, B22, I8)

- `Combobox` primitive with gallery entries in `/ui-kit`. It is adopted in Add product
  (offerings), Change offering (offerings) and the number pickers.
- B9 and B22 per decisions.
- Tests: Combobox (type to filter, arrows, Enter selects, Escape restores, aria-expanded
  and aria-activedescendant); balance-after with an unbilled charge; scrollIntoView
  called for the draft's offering.

### G9: money dialog parity (B11, B28, P12, P15, P16, I7)

- Per decisions. The refund dialog is reused unchanged from the Payments tab and the
  payment dialog.
- Tests: the pinned top-up body with `logicalResource`; the payment body with
  `correlatorId` and description; mandatory reason errors; the "greater than 0" message;
  the holds-through date for an end at midnight.

### G10: parties and customers (P2, P3, P4, I9, I8)

- Per decisions. Status values come from the vendored TMF632 enum for each party type.
  The party picker in New financial account uses `Combobox`.
- Tests: pinned bodies for New individual, New organization, the party edit with every
  document, and New customer; New subscriber with email and phone.

### G11: billing accounts and atomic New subscriber (P5, P6, I1)

- Per decisions.
- Tests: the pinned Add billing account body from both entry points; delete account from
  the customer dialog including the 409 shown in place; the I1 compensation for a failure
  at step 2, at step 3, and a failed compensation.

### G12: orders (B13, B15, I11)

- Per decisions.
- Tests: the item labels for each action; Save details keeps the dialog open; the
  last-page-first request strings; a write refetches one row.

### G13: bills, balances, billing settings (P14, B14, B20)

- Per decisions.
- Tests: the Account totals list and detail requests; the card KeyValue; "Total" on a
  settled bill; the Used-by count.

### G14: wording and consistency (B12, B16-B19, B25, B26, B27, I12, I13)

- Per decisions. Search the frontend for each raw code the review quotes. `labels.ts`
  and `plural()` get unit tests. Extend the style guard: no `{n} …s` pluralisation
  without `plural()` (regex on template literals ending in a plural noun).
- Tests: a label per code family; the plural helper; the B18 dialog request equals
  today's Deactivate request; the unlabeled-input check over every screen test render.

### G15: data-quality scripts and local seed (D1-D5)

- `ops/staging-data/` (README plus one script per item, see below). The scripts talk HTTP
  to a base URL and sign in as the UI does, with credentials read from the human's shell
  (`BSS_URL`, `BSS_USER`, `BSS_PASSWORD`). They dry-run by default and need `--apply` to
  write. They print every change and are idempotent. Test them against the local stack in
  `tests/test_staging_data.py` (dry-run output, then `--apply` on local data, then a second
  run is a no-op). **Never run against the stand.**
- Local seed (`tests/seed.py`): US names and USD magnitudes. The mobile spec requires a
  number, and every mobile add carries one of 20 seeded numbers (`12025550100`-`119`).
  Categories Mobile / Home Internet / TV and catalogs Consumer / Business, with every
  offering in a category.
- `scripts/mcp-smoke.sh` and the e2e specs retire what they create (lifecycle Retired,
  isSellable false) at the end of their run. Edits under `scripts/` may prompt, which is
  expected.

---

## Data quality (D1-D5): scripts only

The stand's data is not changed by any session. The human runs these after reading their
dry-run output, through `make tunnel` (the app on localhost:18081).

| ID | Script / decision |
|---|---|
| D1 | `rename_accounts.py`: billing accounts whose name does not start with their party's name become `<given> <family> main` (individual) or `<organization> corporate`. PATCH `name` only. |
| D2 | **No script.** Rated history is immutable and bills were issued. History before 2026-10-21 stays, and docs/status.md records 2026-10-21 as the cut-over to USD magnitudes. The next stand reset (a vps-infra operation by the human) rebuilds history from the USD seed. |
| D3 | `retire_test_data.py`: offerings and specs whose name or code matches `^MCP `, `TMF-E2E-`, `Prepaid Verifier`, `MCP Coverage`, `MCP-DEMO-`, `MCP Isolated` become Retired and isSellable=false. Test billing accounts with no open product become Closed. Nothing is deleted. The data the GUI test created stays, as the review asks. |
| D4 | `seed_numbers.py`: creates numbers `12025550100`-`12025550119` (skipping existing ones). Creates the spec "Mobile Voice & Data (numbered)", which requires a number, and offerings "Mobile S" `MOB-NS` and "Mobile L" `MOB-NL` with the current USD prices of MOB-S/MOB-L. Existing mobile products cannot get numbers (decision B3). |
| D5 | `catalog_tree.py`: reuses "Mobile Plans" and "Consumer Catalog 2026" when present. Creates the categories Home Internet and TV and the catalog "Business Catalog 2026", and assigns every active offering by specification name. |

---

## Track C: needs the human

These are outside the orchestrated run. They touch the Makefile, compose files, the
harness or the VPS, which need a reviewed diff or the human at the terminal. The order
is the priority:

1. **Release checks** after the human deploys R16 (below).
2. **Restore drill** from the first cron-made dump (status.md "Open checks").
3. **DB pool confirmation on the VPS** (at most 10 sessions; it answers after the nightly
   reboot).
4. **APP_PORT / `make down` profiles** (status.md spec of 2026-10-03), then the vps-lab
   guard backport and the two vps-lab-template fixes.
5. **pytest container `(unhealthy)`**: disable the healthcheck for one-off runs in compose.
6. **Network isolation** of app/MCP (vps-infra).
7. **`"ask"` rules** verification and **cloud startup validation** (docs/cloud.md).
8. **Progress drift enforcement and per-item deployed status.**

The subscriber-card visual review in the CLAUDE.md backlog is folded into the release
checks.

## Release checks

R16 writes these into `docs/ui-redesign-release.md`. After the human deploys, `/ship`
runs its smoke and the human (or a browser session) checks at 1366×768 and 1920×900:

- B1: Suspend today, then Resume today, shows the mapped refusal in the dialog, and Undo
  suspension restores `active`.
- B2: the primary is reachable in New category, Edit subscriber with media, New offering
  and Add product.
- B3/P13: on a numbered offering (D4 run first, or one created in the GUI), a prepaid
  subscriber with a number transfers credit to another.
- B4: a single-product Actions menu shows every item.
- The subscriber card review (figures, bill dialog, Record payment, New subscriber,
  Edit).
- The P1-P16 table, each row marked done or not built with its reason (P12 voucher: the
  backend has no voucher management; P16 value date: `paymentDate` is read-only).
