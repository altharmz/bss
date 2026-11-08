# Frontend (admin UI, stage 5): decisions and conventions

## GUI review fixes

- G8 (B9, B22, I8): the sidebar shows no item counts (Subscribers, Product catalog, Billing runs, Orders lose theirs, with the three count requests and `countOrders`); only the failed indicators remain. Deviation from epic D6 ("Counts in mono"). Postpaid Add product (and the Resume and Change offering previews) label the row "Balance after unbilled charges and this one", computed in cents as `(settlement.unallocatedCredit − settlement.outstanding) − first charge` from one `GET .../settlement`, "—" while it loads or fails; the card's `charges` list is not used, it is capped at 20. An edited draft's offering row is scrolled into view once (`scrollIntoView({ block: 'nearest' })`). `NumberPicker` is a `Combobox` (number, plus its name when the name does not repeat it); Add product and Change offering keep their filtered radio lists with price columns - "I8 offerings: satisfied by R10's filtered list", a deviation from the artboard. `/ui-kit` shows the Combobox (`ui.uikit.combobox`). No write body changes.

- G15 (D1-D5): `ops/staging-data/` holds the four dry-run-first scripts the human runs against the stand (`rename_accounts.py` D1, `retire_test_data.py` D3, `seed_numbers.py` D4, `catalog_tree.py` D5; D2 is no script: history before 2026-10-21 stays, the next stand reset rebuilds it from the USD seed). They read `BSS_URL`, `BSS_USER`, `BSS_PASSWORD` from the shell, sign in as the UI does, print every change, write only with `--apply`, and a second run is a no-op; `tests/test_staging_data.py` runs them on a throwaway database (the dev compose mounts `ops/staging-data` read-only for it). A session never runs them against the stand. D3 treats a billing account as a test account when its name or its customer's name matches the same patterns, and keeps one with any product that is not terminated, cancelled or aborted. The local seed now uses US names and USD magnitudes (a tenth of the old amounts), an organization's account is `<name> corporate`, the specification "Mobile Voice & Data (numbered)" requires a number, 20 numbers `12025550100`-`119` exist and every mobile add carries the next free one, and the categories Mobile / Home Internet / TV hold every offering and are listed by the catalogs Consumer catalog and Business catalog. `scripts/mcp-prepaid-smoke.py` and the numbered-transfer e2e retire the offering and specification they create (Retired, isSellable false); `scripts/mcp-smoke.sh` creates nothing.

- G13 (P14, B14, B20): Balances adds an independent Account totals list and fresh GET detail, with account filtering and currency; the prepaid Balance actions tab reads the account's accumulated balance into a KeyValue with its own loading/error/retry state. Bill lists and details label amountDue as Total and retain Remaining. Built-in Monthly usage counts loaded accounts with the default cycle. Component regressions and backend read replay cover these paths; no writes, API or primitives change.

- G11 (P5, P6, I1): Add billing account (customer dialog and subscriber card More actions) reuses the account step of New subscriber and `createBillingAccount`, so its body is the same; the customer dialog lists its accounts with an inline-confirmed Delete (the 409 shows on the row) and Delete customer replaces itself with "Remove billing accounts first" while any remain. New subscriber stays three TMF POSTs and compensates newest first (account failure: DELETE customer, then party; customer failure: DELETE party); a failed DELETE stops the undo and the error lists the leftover ids with a link to Parties. Component tests and `tests/test_ui_requests.py` pin the bodies and the DELETE sequence. Already present: `deleteBillingAccount` from the subscriber card. No backend/API change.

- G12 (B13, B15, I11): Orders labels each item by its business action, prefers snapshot names and lazily resolves missing product names once per product in the open dialog, with target status beside item state. Save details reloads only the order, retains the dialog and edit panel and reports Details saved in the footer. The global list probes X-Total-Count, reads/reverses the tail page and walks backwards, keeping rows through subsequent loads with visible refresh errors. Writes refresh only the changed row (deletes remove it) and existing navigation counts. Component regressions and backend read replays cover these paths; write bodies and the API are unchanged. The guard rejects the prescribed targeted npm invocation, so supported Make targets and the commit hook provide verification.
- G14 (B12, B16-B19, B25-B27, I12, I13): `format/labels.ts` holds the human labels of API codes (payment methods, run triggers, event types, `plural(n, noun)`, `agoLabel`); screens show the label and keep the raw code in `title` (`Coded`, `StatusPill`). The style guard rejects a `${n} <plural noun>` template without `plural()`. `Checkbox label=` renders one `<label class="bss-check">`; name cells ellipsize with `title`, date/period/id cells never wrap. One form pattern: `DateField`, `SelectField`, `FileField`, payment method as `Segmented` (Record payment, Record refund, top-up); New category/catalog sit in the PageHeader actions; Release hold confirms with the primary style. The test setup fails any test that leaves a form control without a label or aria name. B18 (Deactivate opens End on a date with today) and B19 (toast action contrast, checked against both themes) already held. Write bodies are unchanged, so `tests/test_ui_requests.py` needs no new body.

- G9 (B11, B28, P12, P15, P16, I7): shared positive-money errors check the sign before precision, including grouped zero; Top up optionally sends an active account number as logicalResource; Record payment sends trimmed correlatorId and description without paymentDate. Adjustments require a reason and refunds require a description in the UI. Payments header and subscriber/global payment details reuse the existing account-level refund dialog and refresh the relevant account/refund list on close. Holds through displays the business-zone exclusive end date minus one calendar day. Component and backend replay tests pin the changed bodies; midnight and browser-zone regressions cover the date. Already present: voucher refusal and business-clock payment dates. No backend/API or migration change.

- G7 (P7, P8, P9, P10, I3, B21, B27): New offering sends description, isSellable (default true) and selected category references with explicitly labelled controls; non-sellable catalog rows show a Tag in every lifecycle group. Offering detail Add price supports oneTime and monthly recurring only, allows today or later, posts the price and then patches the complete old-plus-new reference list. Existing offering and price Delete flows remain unchanged. Add product and Change offering choose shownPrice at the selected start/change date's business-zone midnight from the already loaded catalog, without a catalog refetch. The sidebar link is Financial accounts, and a gallery regression requires every form control to have a label. Component tests and tests/test_ui_requests.py pin every new or changed TMF620 write, including exact decimal tokens and preserved price references.

- G6 (B7, retry checks): billing-run history pages 20 runs at a time through the existing offset/limit endpoint, with Previous/Next controls and a range footer. The latest-run panel survives older-page views, selection closes when its run leaves the page, and queued work returns to page one for polling. Component tests walk 23 runs and pin the already-present failed-run and failedTechnical-command Retry POSTs without bodies; no write or backend request changed.

- #89 browser prerequisites: numbered transfer waits for the authenticated catalog
  before reading currency and uses the canonical customer API. The local fixture
  prepares a fresh, unordered offering at business midnight through the TMF API;
  the browser attaches its newly created numbered specification and still verifies
  both purchases and transfer completion with the real temporary billing worker.
  The prepaid smoke opens the product Actions menu and follows Top up into its
  dialog. No application date rules, scenario assertions, retries or hooks are relaxed.

- G5 (B8, B23, B24, I6, I10): nextChargeDate uses the billing core’s due calculation and stored charges, so uncharged first periods point to tomorrow. Customer no. and Starts clarify identity and future starts. Prepaid Add product uses balance-payment wording and hides cycle context; New subscriber hides the API-required cycle-day default. Immediate Add product and Resume at/after paid-through require a funded server preview and offer the existing Top up handoff. Overview reuses Orders’ account/state pagination for drafts, acknowledged and in-progress orders; drafts show the attached recurring catalog price at requested-start midnight and Open reuses OrderModal. Tests cover timing before/after billing, prepaid renewal, wording, gating/handoff and draft prices; all write bodies remain unchanged. Already present: Add product’s Top up handoff. Deviation: no pending-order Activate action exists in this revision (only Add product Activate and order-detail Submit), so there is no third Activate caller to gate. The targeted container frontend command is guard-blocked; use make targets and the commit hook.

- G4b (B3/P1, P11, P13): specification create/edit gains Requires a subscriber number and descriptive characteristic rows (name, type, description, configurable, comma-separated values with first default); create/patch/remove requests are pinned on both sides. An ordered-spec 409 shows the server text, restores and locks only the requirement; other edits remain available. Existing unrelated resource references and unedited characteristic documents are preserved. Product number, New subscriber and transfer refusal explain number eligibility. `e2e/numbered-transfer.spec.ts` creates a numbered spec, offering and two fresh prepaid subscribers, tops up, buys numbered products, and waits for a transfer by number to complete; written now, execution belongs to R16. Deviation: TMF620's `CharacteristicValueSpecification` discriminator omits `BooleanCharacteristicValueSpecification` despite defining that subtype; the real API rejects its `value` with 400 unknown attribute. Boolean type/configurable metadata is supported and replay-tested with an empty value list; predefined boolean values are disabled with an explanation. No backend or contract change. The prescribed targeted container npm command is guard-blocked; allowed make targets and the hook verify the frontend.

- G4a (I2): Change offering reads the current offering's detail, including when retired, and shows only sellable alternatives with the same specification. An empty list explains that no other offering uses this product's specification. TMF622 `numberRequirementDiffers` has mapped refusal copy and code-parity coverage. New component regressions cover mixed specifications, a retired current offering and the empty state; the detail read has backend replay coverage. Existing write bodies and markers stay unchanged. Prepaid offering-change fixtures now give the target specification the same number requirement as the purchased plan, preserving all renewal and number assertions. G4b retains B3/P1, P11 and P13. The prescribed targeted npm command was guard-blocked; allowed make targets and the hook provide verification.

- G3 (B6/B10): the API WriteLock counter spans shared dialog writes and transport requests, pauses background polling, and queues card reloads until writes finish. The card stays mounted during account refreshes; product-change, number and balance-action dialogs are owned by card state using stable ids. Successful account/offering create/delete and every order POST refresh the existing navigation version and its count resources. A resolved write callback that sends no request shows “Not sent. Try again.” Existing request bodies are unchanged and already replay-pinned. Regressions cover a hold confirmation surviving a real intervening reload, polling pause, nested locks, unsent callbacks and count refreshes. The prescribed targeted npm command remains guard-blocked from earlier slices; allowed make targets and the hook provide verification. No new action, field-group or list markers.

- G2b (B1/I5 Undo portion): the product menu offers same-day Undo suspension/resumption from the internal eligibility field instead of the opposite status action. The existing cancelProductOrder body is pinned in component and backend replay tests; dedicated future-suspension Undo regressions preserve the already-present pending action. Guard.sh rejects the prescribed targeted container npm command, so allowed make targets and the hook provide verification. No primitive change.

- G2a (B1/I4 refusal portion): all TMF622 order callers preserve codes and show mapped operator explanations via the D7 Footer; backend AST/JSON parity pins all 23 emitted refusal codes. Suspend → successful reload → refused Resume coverage confirms the action-row Note layout was the defect in that sequence; refused submits do not reload the card. Status/end parametrized coverage plus add, offering, number and draft-submit screen tests retain inputs and requests. Edit draft has Save draft only; submission belongs to its order detail and uses the same mapping. Deactivate reuses the existing End on a date dialog (B18 overlap needed to keep refusals in a dialog), retaining today's delete body and later dated body. G2b retains all same-day Undo/internal API work and I5. Tests are grouped with each screen's fixtures rather than one cross-screen parametrized test; prescribed targeted npm invocation remains guard-blocked, so verification uses make targets and the hook.

G1b browser verification also updates stale pre-redesign smoke selectors for navigation,
buttons, mounted tabs, MoneyStrip/shortfall and heading scope, preserving the scenario
assertions. The “already active” offering badge is informational and repeat purchases are
supported; removing the smoke-only exclusion makes repeat runs work without new fixtures
or changed business rules. `make web-e2e` passed all eight browser tests. Dialog screenshots
at 1280/1600 confirm visible actions and no horizontal overflow.

- G1b (B2): Dialog/Modal use a bounded flex column with a scrolling body and fixed header/footer. DialogFooter portals the two loaded legacy form footers while keeping their state, and the Users native form retains validation through an explicit form association; every Modal caller opts into its enabled footer primary's existing click path. Single-line Enter excludes textarea/non-text controls, composition, disabled/destructive actions and transfer confirmation. Component coverage pins structure and exclusions; dialogs.spec.ts checks New category, Edit subscriber with media, New offering and Add product at 1366×768/1920×900. Requests and markers unchanged. Targeted npm command remains unavailable under guard.sh (G1a); permitted make targets and the commit hook provide verification.

- G1a (B4, B5): all Menu lists now portal to document.body with fixed trigger placement, flip-up and scroll/resize dismissal; keyboard, outside click and focus return stay. Subscribers derives its header currency from all loaded accounts and Amount displays each row's currency. MoneyField requires an explicit currency; a TypeScript AST guard rejects currency literals outside tests/gallery. New component coverage and menu screenshot assertions pin these behaviours; make web-shots passed and Subscribers, prepaid/postpaid menus (including the single-product card) and UI kit were reviewed at 1280/1600 with no horizontal overflow. Requests and markers unchanged. G1 splits by ID: all B2 dialog/Enter/e2e work remains in G1b. The prescribed targeted container test command was rejected by guard.sh; verification uses make targets and the commit hook.

## Ledger v2: visual redesign (designed 2026-10-26)

- Built R16 (#64): every `make web-shots` PNG (light and dark, 1280 and 1600) reviewed against the artboards rendered from the handoff, or against D1-D13 and Components/States where no artboard exists; evidence in [ui-redesign-release.md](../ui-redesign-release.md). CSS fixes: the card's "Next charge" caption wraps inside Since; Billing runs outcome names are sans 500 ink-1; the inspector's Prices table spans the details grid; Bills' Kind does not wrap; a checkbox in `.bss-form` stays one row; long Add product offering names ellipsize after the specification note has given way; `.bss-write-fields.bss-modal-body` keeps the 20 px body padding; a cell's `span.ellipsis` clips (inline-block, so it still lets the row click through). `make web-e2e` green: selectors follow the design ("Financial accounts", "Cash", `/^Top up/`, merged name/code cell, paged "Showing a–b of n", R15 double click / Enter at ≥ 1536 px) and the prepaid smoke picks a free number for the numbered seed offering; no assertion weakened. The shots wait for skeletons on catalog tabs and add `subscribers-inspector-1600`. Deviation: the card's Charges and Transactions Date columns widen from 92 to 108 px in their TSX column lists, because a grid track is set there, not in CSS. Behaviour and wording defects found on the way are #178.
- Built R15 (#62): `useMediaQuery(WIDE_QUERY)` (>= 1536 px; false without `matchMedia`) switches Subscribers and Catalog to a 400 px inspector. Subscribers: a click selects the row, Enter or a double click (`TableRow onActivate`) opens the card, Escape closes; content comes only from the loaded list. Catalog: `Dialog inspector` renders the same `OfferingModal` body, requests and header menu in place (`ui.catalog.inspector`), its lifecycle actions stay in the panel footer. Deviations: the Subscribers Products line is "N active" without offering names, because the loaded product list requests only `fields=billingAccount` and a wider field list would change the pinned request; the Catalog actions stay in the footer, not the header. `make web-shots` not run in this slice.

- Built R14 (#60): Users, Integrations/System, Sign-in, Change password and Not found on the shared primitives; `legacy-tokens.css`, `PageTitle` and `Modal` deleted (every caller uses `PageHeader`/`Dialog`); style guard (d) every `var(--…)` is defined and (e) no `style={{` except `'--…'`; dark theme per D3 (Theme item in the operator menu, `bss.theme`, pre-paint script, `make web-shots` writes `-dark` PNGs). Deviations: the `--fs-*` aliases became `--size-micro/label/body/section/page` tokens in `tokens.css` (rules that set only a size keep their line height); the Theme control is a pressed-button group, not radios, so existing `radio` queries stay unambiguous; the shots script drops two seed-dependent assertions (transfer step 1 text, a 1.00 amount). No request changes.

- Built R12 (#54): Sweep 1 restyles Products, Orders, Numbers, Parties and the subscriber forms with PageHeader filters, page-level tables, `Id` (PRD-, ORD-), D4 `StatusPill` kinds without raw-status labels, M dialogs (L for order details) with `KeyValue`, compact item/cancellation tables, `Field`/`SelectField`/`Checkbox`, and `Segmented as="radio"` for the subscriber type and account mode. Dialog titles keep their accessible names (`Order XXXXXXXX`); the copyable `Id` also appears in the context line. Number allocation/usage/administration states are Tags (D4 has no number kind). Requests are unchanged. TEST-CHANGE: row clicks target the row (the `Id` button copies), type/mode switches are radios, pills use kind labels, order prices render through `Amount`.
- Built R11: M money dialogs with currency-aware MoneyField, outcome/busy labels and loaded Balance after figures; three-step Recipient → Amount → Confirm transfer with recipient status/ID/mode, before/after KeyValue, finality warning and acknowledgement. Enter cannot activate the final transfer. Record payment uses shared refused/unsure Footer with Refresh. Exact bodies, submission keys and polling remain unchanged. Deviation required by existing behavioral contracts: prepaid money actions and transfer retain their safe idempotent Retry on an uncertain result rather than generic Refresh. Tests cover outcome labels, balance arithmetic, acknowledgement/Enter gating and Record payment uncertainty. Read-only screenshots cover money forms and transfer steps at 1280/1600.

- Built R10 (#50): L Add product Dialog with subscriber/mode context, selectable offering rows and left-side number/start fields beside a dedicated server first-charge rail. Labels are Add product before selection and Add {offering} afterward, with Adding… while busy. The footer shows the business-zone preview response time; immediate prepaid shortfall disables secondary Add and moves Top up {amount} {currency}… to primary, while future shortfall retains the information hint and enabled Add. Save draft changes remains the draft primary. Component tests cover radio semantics, outcome labels, funding branches and preview time; browser and seeded screenshot selectors cover both widths and deterministic prepaid shortfall. Requests, bodies and existing markers are unchanged. The shared legacy Preview remains for Resume and Change offering. Tracker is epic #43; no removed tracker rows recreated.

- Built R9 (#48): counted catalog PageHeader, lifecycle groups/counts/hints, code search, five-column page table, Tabs, L offering detail with stepper, price figure, KeyValue, compact price history and header edit/delete Menu; other catalog tabs use page tables and catalogJob pills. Requests, lifecycle confirmations and all write bodies stay unchanged. Tests cover order/counts/hidden groups, code search, all stepper states, lifecycle Note and menu actions. Missing-field deviations: CatalogOffering has no lastUpdate or lifecycle transition dates, so Updated is “—” and step captions show sellability rather than invented “since” dates. Specification and validity remain accessible in rows and in details. Tracker is epic #43; no removed tracker rows recreated.

- Built R8 (#46): Billing runs PageHeader, failure-specific primary Retry, latest-run counts/charged/duration band, business-zone next-run countdown, compact selected history and inline outcomes with copyable account IDs and completion times. Request paths, queries, writes, refusals and polling stay unchanged. Under “When the spec is wrong”, outcomes keep the existing lazy reads: latest is selected initially, but Accounts or a history selection loads details/accounts; automatic loading would violate rule 1's request-timing contract. Catch up retains its visible date input and accessible Catch up action instead of introducing a new dialog. Start/end, charge count and raw failure values remain available; run summary details move to a disclosure. D11 omits per-account Retry and invented grouped failure reasons. Tests cover selection/lazy reads, default failure filter, retry labels, blocked reason, fixed-context/DST countdown and existing request/state contracts.

- Slice 7b: compact product/status/since rows, ellipsis actions, full-width pending-change/Undo sub-rows and stable Overview accepted-command steps with last-check time and capped account links. Requests, 5 s / 3 min polling and terminal dismissal/toasts stay. Missing-field deviation from 7a remains: AccountProduct exposes no number value; preserve its lazy dialog action. Next charge stays as a Since caption to retain the existing value.

- Built slice 7a: Recent activity rail reuses the first eight loaded transactions in server order, with signed amount, date-only business date, balance after, prepaid footnote and All transactions tab link; no requests changed. Tests cover eight-of-twenty, figures, dates, no-refetch navigation and empty/prepaid copy. Split slice 7 for size: compact products/pending subrows/action menu and stable Overview watch steps remain in 7b. Missing-field deviations: transactions expose business dates, not timestamps; retain those dates. AccountProduct has no subscriber-number field; 7b must omit the number value rather than add a request, preserving the lazy Subscriber number action.

- Built slice 6b2: hash-linked subscriber tabs, mode-aware fallback, all existing panels in hidden mounted panes, loaded list counts and prepaid failed-command info badge, full-width Account summary and Overview main/rail grid. Regression tests navigate panes while retaining exact request/state/value assertions; added pane markers, deep-link/fallback, hashchange, counts and no-refetch coverage. Eager requests, polling and dialog ownership are unchanged. Legacy products remain until slice 7; its activity/progress content is still pending. Deferral: accepted-order/transfer watches stay mounted in SubscriberCardScreen above Card, including across card reloads, until slice 7 places the In progress box in Overview while preserving that poll lifetime. All ordinary card panels are in their assigned tabs.

- Built slice 6b1: prepaid ledger/reserved/available MoneyStrip, paid-through and shortfall, postpaid credit-limit boundary/cycle/dues strip, existing money actions and balance-error Note, and semantic Account KeyValue with Refund and financial-account link. Tests cover equation, marker/value separation, primary actions, exact/above/below/no-limit boundaries and definition pairs. Split 6b for size: panel tabs, hashes, loaded counts, Overview grid and remaining tests are explicit in 6b2. Record payment stays a secondary prepaid strip action as D12 requires; eager loaders and HTTP requests unchanged. No design deviations.

- Built slice 6a: subscriber identity header without a panel, account/customer pills, uppercase mode Tag, metadata links and daily prepaid schedule; secondary Add product and More actions with postpaid payment and conditional negative Delete. Split slice 6 for size: both money strips, Account summary and all tabs/panel migration stay in 6b. Existing money entry buttons and panels remain until 6b; requests unchanged.

- Built slice 5: Subscribers PageHeader/filtered count, icon search and slash shortcut, wrapping counted state/mode filters, descriptive no-results, seven-column page table and strict over-limit Flag. Existing prepaid marker moves to Mode. Boundary, shortcut, placeholder, no-results and filtered-count tests added; requests unchanged. No design deviations.

- Built slice 4: Ledger sidebar, brand/environment Tag, active-item hairline, operator footer and breadcrumbs/business-date chip; context-only zone-aware drift and initials tests. Missing-field deviation: Session and Context expose no display name, so the footer shows the session login and its first two uppercase characters. Requests and menu behavior unchanged.

- Built slice 3b2b2b2: every legacy note caller now uses Note with text/markers preserved; static footer styles removed. Shared Footer adds D7 refusal/unsure notes, primary Refresh replacement and stable-width busy actions/disabled fields; four write-state previews and Numbers capture coverage added. Deviation under “When the spec is wrong”: ApiError lacks a server code, so live refusals omit it; optional presentation-code rendering is tested directly and previewed without altering the loader. Gallery uses one column at 1280 and two at 1600 so fixed M dialogs fit without horizontal scrolling. Requests unchanged.

- Built slice 3b2b2b1: S/M/L Dialog, context and step header, padded body, quiet close control and subtle footer; Modal maps widths at 440/620 boundaries. Static size gallery and width/semantic tests cover the foundation. Split 3b2b2b for size: legacy Note classes occur across more than the spec's nine files; all caller migrations, D7 Footer and write-state gallery remain explicitly in 3b2b2b2. No requests changed.

- Built slice 3b2b2a: bottom-left success toast with optional action, single-message replacement and six-second lifetime, plus shared static/live gallery previews. Split 3b2b2 for size; Note callers, Dialog/Modal mapping and D7 Footer/write-state gallery remain in 3b2b2b. No request changes or design deviations.

- Built slice 3b2b1: semantic KeyValue, prepaid/postpaid MoneyStrip, PageHeader with PageTitle compatibility, and Ledger Panel geometry; summary gallery and seeded pages reviewed at both widths. Split remaining 3b2b scope into 3b2b2 (Note caller migration, Dialog/Footer/write states and Toast) for size. The summary strip wraps its contents when the viewport cannot fit the artboard’s full-width equation and actions, preserving the no-horizontal-scroll requirement. No request changes.

- Slice 3b2a built Note (info/warning/negative icons, roles, lead and code) and stable business-zone failure timestamps; gallery reviewed at both widths. Split remaining primitives and screen migrations into 3b2b. Deviation: bootstrap ErrorBlock has no system context, so omits its timestamp rather than assuming a zone; call and Retry remain available. No requests changed.

- Slice 3b1 built keyboard Tabs/TabPanel and Menu with outside dismissal, focus return and disabled items; Table gains structured range and action menus; ProductActions uses the shared Menu with its existing accessible label. Gallery and card reviewed at both widths. Remaining slice 3 scope is explicit in 3b2; no API deviations.


- Built slice 3a: shared table density, sticky headers, keyboard activation/selection, 200 ms delayed skeletons, descriptive query/filter no-results and gallery previews. Split slice 3 for size; remaining primitives, structured footer range, Menu row actions and error timestamp are explicitly tracked in 3b. Existing callers without query keep their accessible “No matches” text until screen migration; query-aware callers use the new design copy. No requests changed.

- Built slice 2b2: migrate every legacy Pill and danger-outline caller, remove compatibility and status tokens, adopt neutral-positive Amount and copyable Id in Subscribers/card, and add gallery examples. Static styles in touched screens moved to CSS; runtime progress and category indentation use CSS variables. Subscriber no. stays alongside the new BA Id column until slice 5 restyles the columns. Capture coverage now includes Accounts and Billing settings. No request or behavior changes.
- Built slice 2b1: explicit D4 StatusPill mapping and all eight CSS glyphs, Tag/Flag/Badge and gallery coverage for every status. Split 2b for size: screen migrations, legacy deletion, Amount/Id and their gallery examples remain in 2b2. No design deviation.
- Built slice 2a: Ledger buttons, fields, filter/radio Segmented and the initial `/ui-kit` controls gallery. Slice 2 split for size; status/amount/id and caller migrations remain in 2b, including removal of temporary danger-outline compatibility. No product or request changes.
- Built slice 1: Ledger tokens, font faces, legacy aliases, style guard and `make web-shots`; automatic dark preference remains disabled until slice 14. No design deviation.

The first handoff (`design-ref/design_handoff_bss_admin/`) covered 4 screens and 2 modals.
The other screens grew without a designer. The UI is restyled from the second handoff,
`design-ref/design_handoff_bss_admin_v2/` ("Ledger"). The redesign covers:
- a 9-step type scale and warm-grey roles;
- tables on the page;
- 8 status families with glyphs;
- one primary action per view;
- a new information architecture for the Subscriber card (money strip and tabs);
- dark mode from the same tokens.

It is **visual only**: API calls, bodies, routes and polling are unchanged, so
`tests/test_ui_requests.py` is untouched.

The human delegated every decision; they are in epic #43 (rules and D1-D13; the redesign
is done and the epic closed 2026-11-05). The ones that still matter are:
- **D4:** every TMF state (TMF620/622/629/632/637/654/666/676/678) and every internal
  state maps to a status family, with an explicit kind per call site.
- **D11:** artboard content that needs data or requests the API does not offer is
  dropped or replaced: CSV export, per-account run retry, claim expiry, request id,
  "customer since", catalog "In use", the card's Orders and Products tabs, and transfer
  step 4 inside the dialog.
- **D9:** the 5 s / 3 min polling is kept.
- **Rule 1:** no HTTP request is added or changed.

Progress: done (epic #43's task list, closed 2026-11-05); each slice has a line below. Wherever its values differ from the v1 handoff, this entry wins.

## Next slices 1-24: designed (2026-10-24)

The human delegated every design decision on 2026-10-24. The UI for the remaining
prepaid actions and for MCP parity is designed slice by slice (done; see `git log`). Slice 10 supersedes "No writes in this screen"
below. Each slice adds its own entry here when built.

## Slice 1: prepaid first-charge preview in Add product (2026-10-24)

Built as designed; frontend only,
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

Built as designed (the backend
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

## Slice 22: Balances screen (TMF654 reads) (2026-10-25)

Built as designed, frontend only.
`screens/Balances.tsx` (route `/balances`, sidebar Billing > Balances) and `api/balances.ts`
(buckets) and `api/history.ts` (`loadKindActions`, `loadKindAction`): five tabs, each with a status
filter and an account chip (`partyAccount.id`). The buckets tab joins `GET bucket` with
`GET accumulatedBalance` by account; the detail dialog reads both by id. The four action tabs read
their kind's list; a row opens the kind's GET in the same Balance action dialog as the card, so the
cancel, release, remove and note writes come with it. `ActionDialog` (`PrepaidHistory.tsx`) is now
exported and takes a `load` function. The dialog is re-keyed after a write, so the detail and the
list reload.
Tests: `Balances.test.tsx` (the buckets join, status and chip filters, bucket detail, each kind's
own list only, top-up cancel with its pinned PATCH body, transfer receiver named through its bucket),
`tests/test_ui_requests.py::test_balances_screen_reads_as_the_ui_makes_them` (every query replayed).
Deviations (the design's facts against the code and the vendored TMF654 spec):
- The vendored `listBucket` declares no filter, but the server filters buckets by `status`,
  `usageType`, `id` and `partyAccount.id` (`app/api/balance.py`); the screen uses the server's.
  Follows the code, as the spec rule says.
- The buckets table has a Total column: the accumulated balance the join supplies (the design named
  remaining, reserved and status only).
- A transfer row reads "Transfer to {receiver}" and resolves the receiver with one
  `GET bucket/{id}` per distinct receiver bucket of the list (a failure shows "another account").
- A hold row shows Held or Released, as on the card.

## Slice 21: Product and number inventory (2026-10-25)

Built as designed, frontend only.
`screens/Products.tsx` and `api/products.ts`: the `/products` screen (Products > Products) lists
`GET product` over every status with the server filters `status` and `billingAccount.id` (the
`?account=` chip) and client filters on offering name and number. A row opens the detail from
`GET product/{id}` (dates, specification, subscriber link, `productOrderItem[]` with a link to
`/orders?account=`, the realizing resource). The Numbers detail now loads `GET resource/{id}` and
offers **Delete number** (`ui.numbers.delete`, then Confirm delete): DELETE without a body through
`useWrite`; hidden when the loaded data shows an assignment (a product, or allocation not
`available`, or usage not `idle`), otherwise the server's 409 is shown in the dialog.
Tests: `Products.test.tsx`, `Numbers.test.tsx` (delete pinned, refusal kept in the dialog),
`tests/test_ui_requests.py::test_product_inventory_reads_and_number_delete_as_the_ui_sends_them`.
Deviations: the product list has no number value, only the resource id, so the Number column joins
the inventory list (`loadNumbers`); order links go to `/orders?account=` because the Orders screen
filters by account only; the status filter is a select of the eight stored statuses.
TEST-CHANGE: the shell test's expected sidebar links gain `/products`; the first Numbers test stubs
the new `GET resource/{id}`.

## Slice 10: Order writes: cancel, drafts, order details (2026-10-25)

Built as designed, frontend only. It
supersedes "No writes in this screen" of the 2026-10-21 decision: the human delegated the writes on
2026-10-24 and TMF622 defines them for clients. The order dialog (`screens/Orders.tsx`) offers Cancel
order (`cancelProductOrder` with the order and an optional reason), Edit details (PATCH of the changed
description/category/priority, null for a cleared one), and for drafts Submit, Edit draft and Delete
(Delete also on rejected). Add product gains Save as draft (`requestedInitialState: "draft"` under a
fresh Idempotency-Key) and an edit mode (`DraftToEdit`, button Save draft) that PATCHes
`productOrderItem` and `requestedStartDate`. `ToastProvider` now takes any node, for the toast link.
Tests: `Orders.test.tsx` (actions per state, every body, 409/rejected/unsure messages, edit round
trip, in-progress follow), `SubscriberCard.test.tsx` (save as draft body, header, toast), and
`tests/test_ui_requests.py::test_order_writes_replay_as_the_ui_sends_them`.
Deviations:
- The toast after Save as draft links to `/orders?account=<id>`, not to the order: the Orders screen has
  no per-order link.
- A submitted prepaid draft that is `inProgress` is followed by the existing `OrderWatch`, shown above
  the Orders list (the subscriber card's watch is not involved).
- The Orders state filter already offered `draft`; nothing was added.
- The Cancel POST and the PATCH/DELETE carry no Idempotency-Key (a repeat of a cancel is a 409); only the
  draft POST does. An uncertain result shows "Unsure whether it was saved" with Refresh.

## Slice 9: Sidebar sections and the global Orders screen (2026-10-25)

Built as designed (see the 2026-10-21
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

Built as designed, frontend only.
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

Built as designed, frontend only.
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

Built as designed. The product row
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

Built as designed, frontend only.
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
the earlier per-account order history. It is read-only
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
- **No writes in this screen** - superseded on 2026-10-25 by slice 10 (entry below): cancel,
  edit details and drafts are in the order dialog. Drafts (create, PATCH, submit, DELETE) and cancelling
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

The human approved this slice of the UI/MCP gap plan (removed; see `git log`).
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
- Postpaid charge timing copy follows the end-of-day rule: an order completed today is charged by
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

## Prepaid lifecycle UI (2026-11-08, issue #218)

Built the product row's server-provided lifecycle state and next transition, including
the lifecycle suspension cause. Billing settings gains a Prepaid lifecycle tab with
GET/partial PUT of the four durations. It reuses the shared edit dialog and write handling:
integer bounds, changed fields only, retained refusal values, and Refresh after uncertainty.
No backend or policy changes. Component tests cover projections and settings, and
`tests/test_ui_requests.py` replays the exact partial PUT including zero and the upper bound.

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
- **Harness:** Node runs only in the web containers (`make web-*`); the harness no longer
  enforces this by hook, the CLAUDE.md rule stands. `settings.json` denies edits under
  `design-ref/`. The format hook runs prettier (in `web-offline`) on edited UI files.
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
  earlier finding #19): a file's first render pays every cold cost (stubbed fetches, JSON,
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

## UI: parties and customers (slice 15, 2026-10-25)

Built as designed, frontend only.
Route `/parties` (`screens/Parties.tsx`, `api/parties.ts`), sidebar Customers > Parties, tabs
Individuals / Organizations / Customers with name search and a status filter. One load holds the
three lists plus the billing accounts (`fields=name,relatedParty`) and the owners of financial,
party and settlement accounts (`fields=relatedParty`), so the delete rules read from loaded data.
The detail dialog GETs the record; Edit reuses `Field` and `CUSTOMER_STATUSES` of
`SubscriberForms.tsx` and patches only changed fields.
G10 adds party creation, customer creation from a party, party statuses, birth date and document fields, optional subscriber contact media, and a shared Combobox for account owners; the existing API already stores all documents.
Tests: `Parties.test.tsx` (lists, search and filter, links, creates, edits, delete bodies, hidden
Delete, server 409), `combobox.test.tsx`, and the party/customer UI request replays.
Deviation: "accounts it owns" is read from the financial, party and settlement account lists
(three extra reads).


## UI: payments and refunds history (slice 19, 2026-10-25)

Built as designed, frontend only.
`screens/Payments.tsx` and `api/payments.ts`: the card's Payments panel with a detail dialog
(`GET /payment/{id}`), and the `/payments` screen (Billing > Payments) with Payments | Refunds tabs,
a status filter, the `?account=` chip (sent as `account.id`) and a client-side date range. Both
lists load with `getAll`, newest first.
Tests: `Payments.test.tsx` (list, links, tabs, filters and requests, detail, error and retry, the
card panel) and `tests/test_ui_requests.py::test_payment_and_refund_history_reads_as_the_ui_makes_them`.
Deviations: `done` is the only payment and refund status, so the status filter has one choice
besides All; the reference column is `correlatorId`, else `authorizationCode`, else `name` (the
payment has no single reference field); refund rows have no account name, so they show `BA-xxxx`.
TEST-CHANGE: the shell test's expected sidebar links gain `/payments`.

## Slice 23: Event subscriptions (hubs) and System panel (2026-10-26)

Built as designed: a backend list,
its MCP tool and the UI. No migration. `GET /internal/v1/eventSubscription` (internal contract
first; `app/internal/event_subscriptions.py`, `hubs.page`) lists live hubs newest first with
`offset`/`limit` and an `api` filter; each item has `id`, `api` {id, name}, `hub` (the API's
`/hub/{id}` path), `callback`, `query`, `eventTypes`, `subscribedAt`, `lastDelivery` {state, at}
(the latest attempted `event_delivery`) and `failures` (deliveries `failed` for good): both come
from a lateral lookup on the delivery index, so no new table. MCP tool `list_event_subscriptions`
(185 tools). UI `/integrations` (`screens/Integrations.tsx`, `api/hubs.ts`), a fourth sidebar
section **System** with Integrations and, for an admin, Users (the Users link left the operator
menu). New subscription: API select, callback, event checkboxes; it POSTs `{callback}` or
`{callback, query: "eventType=A,B"}` to the API's `/hub`. Delete (with a confirmation) sends DELETE
to the `hub` path the server names. The server's 422 shows in the dialog with the values kept.
The System panel (`ui.system`) shows `/healthz`, `/billing-status` as key/value (a 503 body is shown,
not hidden) and the BssBillingAccount schema link. The dev proxy forwards `/healthz` and
`/billing-status`.
Tests: `tests/test_event_subscription_list.py` (list, filter, paging, deleted excluded, delivery
summary, 401), `Integrations.test.tsx` (list, panel, 503 states, role, create for TMF676 v4 and
TMF620 v5, 422 kept, delete), `tests/test_ui_requests.py::test_event_subscriptions_as_the_ui_sends_them`
and `test_the_ui_event_lists_are_the_servers` (`hubs.ts` base paths and events against
`app/events/types.py`; `compose.override.yaml` mounts that one file into the test container).
Deviations:
- The design says eight APIs; the server has ten hubs (TMF620, 622, 629, 632, 637, 639, 654, 666,
  676, 678). The select offers all ten.
- The hub POST/DELETE carry no Idempotency-Key: the hub API journals none (TMF654's hub is not a
  balance action). An uncertain result offers Refresh, as for any other write.
- TEST-CHANGE: `App.test.tsx` expects the System section in the sidebar.

Ledger v2 slice 13 (R13): Prepaid commands (dense table, `Dialog` + `KeyValue`), Bills, Global bills, Payments, Balances and Prepaid history print billing-account, command, payment, balance-action and bill-link ids as `Id` (never `accountLabel`/`shortId` text); payment and balance movements use `Amount` (payments and balance actions `signed`, refunds negative). Deviation: financial, party and settlement accounts, and buckets have no D5 prefix, so their ids stay plain text; "Built in" was already a `Tag`; the order and product ids in the command dialog are no longer links (an `Id` is a copy button).
