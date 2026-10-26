# UI redesign "Ledger" (v2): decisions and slice specs (2026-10-26)

The handoff is in `design-ref/design_handoff_bss_admin_v2/` (read-only). Read its
`HANDOFF.md` first. Then read `tokens.css` and the artboard each slice names. This file
records every decision the handoff left open, adapts the handoff to the backend, and specifies
each slice. **Where this file and the handoff disagree, this file wins.** The tracker is
[ui-redesign-slices.md](ui-redesign-slices.md). The brief that produced the handoff is
[ui-redesign-brief.md](ui-redesign-brief.md).

The human delegated every design decision on 2026-10-26. No slice needs approval.

## Rules for every slice

1. **Visual and layout only.** Do not change API calls, query strings, request bodies,
   headers (including `Idempotency-Key`), routes, polling intervals, permissions or business
   rules. `tests/test_ui_requests.py` must not need any change. If a slice seems to need
   one, it is wrong; see "When the spec is wrong".
2. **Read before writing:** CLAUDE.md, this file's "Decisions" and your slice's section,
   [patterns.md](patterns.md), and the artboard(s) named in the slice. Open other screens
   only as needed.
3. **Artboards are the spec for looks.** The inline `style` values are exact. Map every
   value to a token in `frontend/src/ui/tokens.css`. No hex colour, font size or raw
   shadow in components or screens. Only the 9 `--type-*` tokens set text. Spacing uses
   `--space-*` unless a value is a fixed component dimension (e.g. a row height), and those
   are tokens too. The `renderVals()` arrays are sample data. Copy the artboard's wording
   only where the data exists (see D10).
4. **CSS lives in CSS files.** Do not add new inline `style={{}}`. Slices that touch a screen
   remove that screen's existing inline styles, except a dynamic value such as a grid
   template or a progress width.
5. **Keep the test contracts:**
   - Every `data-ui` marker stays on the element that has the same meaning.
   - A new visible part gets a new marker, added to `docs/progress/scope.yaml`.
   - Accessible names (roles, labels, button text) stay unless this file renames them.
   - A test that changes because a label or structure changed by design gets a
     `TEST-CHANGE: <reason>` line in the commit message.
   - Never delete or weaken an assertion about a request, a state or a refusal.
6. **New tests in every slice.** Components: render, states, keyboard behaviour. Screens:
   the new structure (tabs, groups, flags), queried by role or by `data-ui`.
7. **Look at it.** After the change, run `make web-shots` (added in R1). Compare each touched
   screen at 1280 and 1600 px with its artboard, and fix differences you can see. Nothing
   may overflow horizontally at 1280 px.
8. **Docs per slice:**
   - `docs/ui.md` for changed screens;
   - `docs/patterns.md` when a primitive's API or a screen shape changes (rule from that
     file);
   - `docs/progress/scope.yaml` for new markers;
   - a short line under "Ledger v2" in `docs/decisions/frontend.md` (what was built,
     deviations);
   - this slice's row in the tracker.
9. **Toolchain** (no Claude hooks protect you, so follow it by hand):
   - Node, npm and npx run only in containers, through `make web-*` targets or `docker
     compose run --rm web-offline ...`. Never on the host.
   - Nothing under `design-ref/` is ever edited.
   - Run `make web-fmt` before committing. The pre-commit hook runs `web-lint`, which
     fails on unformatted files.
   - Do not install or upgrade dependencies. No new package. Every component is hand-written.

## When the spec is wrong

- **The design shows data the API does not return:** leave it out, or use the D10
  replacement. Record it in the slice's decision line. Never add a backend change in this
  tracker.
- **An existing test asserts old visuals** (a class name or a tone): update it with
  `TEST-CHANGE:`. **An existing test asserts behaviour** (a request, a refusal, a state):
  the slice is wrong; keep the behaviour.
- **A slice is too large:** split it into `Na`/`Nb` rows in the tracker, each committed on
  its own. Do not widen scope.
- **Stop and report to the human only for these:** a needed backend or API change, a
  migration, a billing behaviour change, or two failed attempts at the same slice.

## Decisions

### D1 Tokens

- `frontend/src/ui/tokens.css` becomes the handoff's `tokens.css`, copied exactly, plus:
  - `--row-height-dense: 28px` (Foundations "Table density" uses 28 for outcomes and the
    command queue; HANDOFF says 32 compact; use 32 for `compact` and 28 for `dense`);
  - letter-spacing companions: `--tracking-page: -0.01em`, `--tracking-micro: 0.06em`,
    `--tracking-figure: -0.01em`;
  - a spinner track for buttons on a solid fill: `--color-on-primary-dim: rgba(255, 255,
    255, 0.35)` (light and dark).
- **The old token names** (`--muted`, `--neg`, `--fs-125`, ...) move into a temporary
  `frontend/src/ui/legacy-tokens.css`. Each one is an alias of a v2 token, with a comment.
  Font sizes snap to the scale: 10.5 and 11 to 11 px, 11.5 and 12 to 12 px, 12.5 and 13 to
  13 px, 14 and 15 to 15 px, 16 to 18 to 15 px (titles inside dialogs), and 20 to 20 px.
  Screens keep working until they are restyled. The file is deleted in R14, and nothing
  may reference it after that.

### D2 Fonts

Self-hosted `@fontsource` only (no Google Fonts link from the artboards). Golos Text 400,
500, 600; JetBrains Mono 400, 500. Remove the Golos 700 and Mono 600 imports and their
uses.

### D3 Theme

- Light is the default.
- **R1–R13:** only the opt-in `:root[data-theme="dark"]` block is active. The
  `prefers-color-scheme` block is commented out with a pointer to R14, because unmigrated
  screens still assume a white surface.
- **R14** enables the `prefers-color-scheme` block. It also adds a Theme choice (System /
  Light / Dark) to the operator menu, stored in `localStorage` (wrapped in try/catch) and
  applied as `data-theme` on `<html>` before first paint (a small inline script in
  `index.html`).

### D4 Status families (TMF-based mapping)

- One component, `StatusPill({ status, kind })`, with one mapping table in
  `frontend/src/ui/status.ts`.
- `kind` disambiguates values that mean different things in different APIs:
  `account | customer | party | offering | product | order | orderItem | run |
  runOutcome | command | bill | onDemandBill | bucket | balanceAction | delivery |
  catalogJob`.
- An unknown value renders as Draft with the raw value as its label, and logs once with
  `console.warn` in development. Never crash.
- Families, glyphs and colours are as in HANDOFF "Status families". Ended pills are
  outlined. The label is sentence case of the API value unless a label is given below.

| kind | Live | Moving | Queued | Pending | Paused | Failed | Draft | Ended |
|---|---|---|---|---|---|---|---|---|
| account (TMF666 state) | Active | | | | Suspended | | | Closed |
| customer (TMF629) | Active | | | | | | Created | Inactive |
| party (TMF632) | validated | | | | | | initialized | deceased, closed |
| offering (TMF620 lifecycleStatus) | Active, Launched | | | | | Rejected | In study, In design/In Design, In test | Retired, Obsolete |
| product (TMF637) | active | | pendingActive | pendingTerminate | suspended | | created | terminated, cancelled |
| order, orderItem (TMF622) | completed | inProgress, assessingCancellation, pendingCancellation | acknowledged, pending | | held | failed, partial, rejected | draft | cancelled |
| run (internal) | completed | running | | | | failed | | |
| runOutcome (internal) | ok → "Billed" | | | blocked → "Waiting on an earlier date" | | failed | | |
| command (internal prepaid) | completed | | pending → "Queued" | | | failedTechnical → "Failed (technical)" | | |
| bill (TMF678) | | | validated → "Ready to send" | sent → "Unpaid", partiallyPaid → "Partly paid" | onHold → "On hold" | | new | settled → "Settled" |
| onDemandBill (TMF678) | done | inProgress | | | | rejected, terminatedWithError | | |
| bucket (TMF654) | active | | | | suspended | | | expired |
| balanceAction (TMF654) | completed | | created → "Accepted" | | | failed | | cancelled |
| delivery (hub, internal) | delivered | | pending | | | failed | | dropped |
| catalogJob (TMF620) | Succeeded | Running | Not Started | | | Failed | | |

Reasons:
- TMF defines `rejected` (order) as a validation failure, and `partial` as some items
  failed. Both need an operator, so they are Failed.
- `held` waits on a person, so it is Paused.
- `pending` in TMF622 is waiting for information, so it is Queued.
- A settled bill is final, so it is Ended: unpaid bills stand out in a mixed list.
- `blocked` run outcomes clear on their own once the earlier date bills, so they are Pending.

Customer and party labels stay as the API spells them. Offering labels use sentence case
("In design").

### D5 Numbers, ids, dates

- `Amount` follows HANDOFF rule 2. It drops the green credit colour: positive is never
  money. An explicit `+` is shown only where the component is given `signed`
  (transactions, payments, balance movements).
- The old `chip` variant goes away. The "Over limit" flag replaces it.
- `Id` component for display ids:
  - The prefix is in `--color-ink-3` and the body in ink-1, in mono. Click copies the
    **full** id (UUID) to the clipboard and shows the toast "Copied".
  - Prefixes: `BA-` (billing account), `ORD-` (order), `BL-` (bill), `CMD-` (prepaid
    command), `PAY-` (payment), `PRD-` (product). Customer numbers stay as they are now
    (`Subscriber no.`, no prefix).
  - The body is the existing display id: the first 8 hex digits, upper case.
- Dates and times stay as `format/dates.ts` makes them (business zone). A relative hint ("in
  19 days") is computed from `context.businessDate`, never from the browser clock.

### D6 Business-date chip (top bar)

- The chip shows `context.businessDate` and the zone label from the context.
- It turns warning-toned with "N day(s) behind/ahead of the wall clock" when the business
  date differs from **today in BILLING_TZ**. Compute that with `Intl.DateTimeFormat`
  (`timeZone` from the context) on `Date.now()`. That is the only use of the browser clock
  in the UI, and a test with a fixed system time covers both states. Re-check every 60 s.
- The operator avatar (initials) in the top bar opens the existing operator menu. The
  sidebar footer shows name and role, as the artboard does. The existing menu items keep
  their names and markers.

### D7 Dialogs and write states

- `Modal` becomes `Dialog` with `size: 'S' | 'M' | 'L'` (400 / 560 / 760). `Modal`
  stays as a thin wrapper (width ≤ 440 → S, ≤ 600 → M, else L) until R14 removes it.
- Header: title (`--type-section`), an optional context line, an optional "Step n of N",
  and the close button.
- Footer: `--color-bg-subtle`, state or reason text on the left, actions on the right,
  primary last.
- Write states, through `useWrite` and `Footer` in `screens/FinancialForms.tsx`. Their API
  stays and gains optional props:
  - **busy:** `Button busy` shows the spinner and keeps its width. A dialog passes
    `busyLabel` ("Recording…"). Other controls are disabled.
  - **refused:** a negative `Note` above the footer: the server's message, then the code
    in mono. Inputs stay editable.
  - **unsure:** a warning `Note`: "Unsure whether it was saved." plus the call (method,
    path) and the time. No request id: the API has none. The primary becomes Refresh, and
    the dialog's normal primary is hidden.
- Final writes (transfer) keep the existing confirmation step and gain an acknowledgement
  checkbox. The primary stays disabled until it is ticked, and Enter does not submit on
  that step.
- Primary labels state the outcome with the amount where one exists ("Record 42.10
  EUR"). Rename labels only where the slice says so, and update tests with
  `TEST-CHANGE:`.

### D8 Toast

- Bottom-left, `--elev-popover`, ink-1 background, white text. Shown for 6 s, with an
  optional action (`{ label, onClick }`).
- Success only. Errors stay inline (already the case).
- One at a time, as now. `useToast()` keeps its signature and gains an optional second
  argument for the action.

### D9 Polling

Keep `POLL_MS` (5 s) and `POLL_CAP_MS` (3 min) from `screens/watched.ts`. The artboard's
2 s / 60 s are not adopted: changing them changes request behaviour (rule 1). The copy
adapts: "Checking every 5 s · last check HH:MM:SS"; when the cap is reached, "Still
running. It continues in Prepaid commands." with a link to that screen.

### D10 Artboard content the API does not have

| Artboard | Decision |
|---|---|
| Subscribers "Export CSV" | Not built. |
| Card "Customer since …" | Not shown: TMF629/666 carry no creation date (an existing decision). |
| Card "Charged daily at 00:30" | Shown for prepaid accounts, from the context's run time. |
| Billing runs per-row Retry, "Tries" | No per-account retry exists. Retry is per run and already retries only the failed accounts. Page primary is "Retry N failed" (the existing run retry). The outcome table has no Retry column; Tries is replaced by the outcome's reason. |
| Billing runs "4 × rating timeout (OCS)" summary | A failure summary counts the outcomes' reasons when the outcome rows carry one; otherwise it shows "N failed". |
| Add product "Reserved for you until …" | Not shown: number claims have no expiry. |
| Unsure "req 7f3a91c2" | Replaced by method, path and time. |
| Catalog "Created … by a.lepp" | Date only. There is no author. |
| Catalog "In use" | "Active products": counted client-side from one `GET /product?status=active` list (the `getAll` pattern Subscribers already uses). If the slice finds this list already loaded nearby, it reuses it. No per-row requests. |
| Order step timestamps | Only what TMF622 returns: `orderDate` (acknowledged), `requestedStartDate` (waiting for start), `completionDate`. Steps without a date show no time. |
| Display ids `ORD-26-10-00418` | `ORD-` + the 8-hex display id (D5). |
| Transfer step 4 Queued → Running → Succeeded | The command has no running state. Show Queued → Completed, or Failed (technical) with the existing Retry. |
| Subscribers search placeholder "Name, BA id or number" | Keep what the search does now (name or subscriber number). If BA id matching can be done client-side on the loaded list, add it and say so in the placeholder. |
| Sidebar user "Elena Kask" | The signed-in operator from the session, as now. |

### D11 Layout

- Minimum width 1280 px.
- The page background becomes `--color-bg`. Tables sit on the page: no panel border, no
  card.
- The page gutter is `--space-xl`, and the first/last table cells align to it.
- The inspector (R15) appears at viewport width ≥ 1536 px, through one `useMediaQuery`
  hook.
- Below 1536 px a row click does what it does now (navigate or open the dialog).

### D12 Subscriber card information architecture

Order on the page:
1. **Header:** name (`--type-page`), StatusPill, mode Tag. Meta line: `Id` BA, customer
   no., party, customer status. Actions: Add product (secondary), Edit, and a `⋯` menu with
   Record payment, Refund, On-demand bill and Delete (only when deletable, as now).
2. **Money strip:** the prepaid or postpaid variant from Components.
   - Prepaid primary is Top up. Adjust, Hold and Transfer are secondary.
   - Postpaid primary is Record payment. Record payment then leaves the `⋯` menu for
     postpaid.
   - Shortfall comes from the existing prepaid figures.
3. **Tabs**, remembered in the URL hash (`#products`), so the route is unchanged. Each tab
   lists what it holds:
   - **Overview:** the products table (pending change as an inline sub-row with Undo), the
     "In progress" order panel (the existing order watch), and the Recent activity rail
     (the last 8 transactions, with balanceAfter).
   - **Products:** all products, with "Show terminated".
   - **Orders:** this account's orders.
   - **Charges:** the charge history.
   - **Transactions:** the transaction history.
   - **Bills:** the bills panel and its dialogs.
   - **Payments:** this account's payments and refunds.
   - **Balance actions:** prepaid only; the existing history and its dialog.
   - **Commands:** prepaid only; the existing prepaid commands panel.
   - **Account:** the existing BillingAccountPanel content (settlement, financial account,
     credit limit, cycle and settings) as a KeyValue block, with its edit actions.
   - Tab counts come from data already loaded. A tab with open work (orders in progress,
     pending commands) shows an info Badge.
4. The rail sits beside the main column from 1280 px (320 px). At ≥ 1536 px it is 400 px.

## Slice specs

### R1 Tokens, fonts, screenshot tool

- **Tokens:** `ui/tokens.css` ← handoff tokens plus the D1 additions. `ui/legacy-tokens.css`
  holds the aliases. `base.css` imports both and loads the fonts per D2. Body: `--color-bg`,
  `--type-body`, `--color-ink-1`. The `.num` class uses `--type-num`, and `.neg` uses
  `--color-negative`.
- **Stray values:** replace the hard-coded `#fff`/`#000` in `ui.css`, `shell.css` and
  `screens.css` with tokens (`--color-on-primary`, `--color-ink-1`, ...). Replace the two
  `var(--x, #fff)` fallbacks in `ProductActions.tsx` with plain tokens.
- **Screenshots:** add `make web-shots`. A Playwright spec (`frontend/e2e/shots.spec.ts`,
  run only by that target, not by `make web-e2e`) signs in like the smoke does. It opens
  these pages at 1280×900 and 1600×1000 and writes PNGs to `.shots/<name>-<width>.png`
  (git-ignored):
  - `/subscribers`;
  - a prepaid subscriber card and a postpaid subscriber card from the seed;
  - `/catalog`, `/billing-runs`, `/orders`, `/bills`, `/balances`.

  It uses the same compose/e2e setup as `make web-e2e` against the local stack with
  `make seed` data. Document it in CLAUDE.md's command list.
- **Tests:** a test that fails if any CSS file other than `tokens.css` and
  `legacy-tokens.css` contains a hex colour or a `px` font-size (a small vitest reading the
  files). Allow existing violations through an explicit, shrinking allow-list file.
  Each later slice removes its entries, and R14 empties it.
- **Done when:** the app renders with v2 colours and type on every screen, tests pass,
  and the shots exist.

### R2 Primitives: actions, fields, status

- **Button:** variants `primary | secondary | danger | quiet`.
  - `danger-outline` becomes `quiet` with negative text: add `tone="negative"` to quiet,
    and migrate the 13 uses.
  - `small` = 28 px.
  - `busy` and `busyLabel` per D7.
  - Focus ring per HANDOFF rule 8.
  - Disabled: `--color-bg-sunken` and `--color-ink-4` (no opacity).
- **Field primitives:** `Field` (label, help, error with the diamond glyph, `htmlFor`
  wiring), `TextInput`, `SelectField` (native select with a custom chevron), `DateField`
  (text input `YYYY-MM-DD` in mono with the calendar icon; keep the existing date input
  behaviour and values), `MoneyField` (right-aligned mono, currency suffix, keeps the
  validated decimal string rules of `format/money.ts`), `Checkbox`.
- **Segmented:** the track style; `count` in mono; radio-group semantics when used as a
  field (`role="radiogroup"`). The pressed-button semantics stay for filters.
- **Status and tags:**
  - `StatusPill` and `ui/status.ts` per D4, with the 7 glyphs in CSS (no SVG).
  - `Tag` (outlined caps: Prepaid/Postpaid).
  - `Flag` (Over limit).
  - `Badge` (count, failed, info "1 open").
  - `Pill` remains as a deprecated alias that calls `StatusPill` with a guessed kind,
    until R14.
- **Amount and Id:** `Amount` per D5, and the new `Id` per D5.
- **Tests:**
  - every row of the D4 table, each `kind`, renders the right family and label;
  - an unknown status falls back to Draft;
  - Amount formats U+2212, 2 decimals and `+` only when signed, and has no green;
  - Id copies the full id;
  - Button busy keeps its name accessible and is disabled;
  - MoneyField keeps the typed decimal string.
- `kit.test.tsx` tone assertions change (`TEST-CHANGE:`).
- **Done when:** Components.dc.html's buttons, fields, pills, tags and badges match in
  the shots (render them on a hidden dev-only route `/__kit`, excluded from the sidebar and
  from the build's router in production: `import.meta.env.DEV`).

### R3 Primitives: table, states, structure, dialog, toast

- **Table** (`ui/Table.tsx`):
  - header 32 px (`--type-micro`, `--color-bg-subtle`, sticky) and rows 36 px, with
    `compact` (32) and `dense` (28) table props;
  - first/last cell padding 24 px; hairlines only; no outer border;
  - hover, `selected` (bg + inset bar), and `:focus-visible` rows (rows that have
    `onClick` get `tabIndex=0` and Enter/Space activation);
  - an optional `actions` column rendering a 28 px `⋯` `Menu` trigger, visible on row hover
    and focus;
  - `TableFooter` gains the pagination layout ("1–50 of 1,284", Previous/Next). The
    existing paging behaviour is unchanged.
- **States:** `ListState` keeps its logic. Restyle it per States.dc.html:
  - left-aligned, inside the table body, with the header kept;
  - the skeleton shows after 200 ms (a timer; tests use fake timers or wait for it);
  - the error shows the call and the time;
  - "No results" names the query and active filters when the caller passes them. A new
    optional prop; screens adopt it in their slices.
- **Structure primitives:**
  - `Tabs` (roving focus with arrow keys, `role=tablist/tab/tabpanel`, counts, badge);
  - `Menu` (button + popover, `role=menu`, arrow keys, Escape, outside click, `…` items,
    destructive items in negative text, separators);
  - `Note` (`info | warning | negative`, bold lead plus text, optional code);
  - `KeyValue` (`dl` grid);
  - `MoneyStrip` (prepaid equation and postpaid variants, an action slot);
  - `PageHeader` (title, count, actions, filter row; replaces `PageTitle`, which stays as
    an alias until R14).
- **Panel:** a hairline border, radius md, 44 px header, no shadow. It is used only for
  grouped work, per Components.
- **Dialog and Footer:** per D7. **Toast:** per D8.
- Replace the inline-styled menu in `ProductActions.tsx` with `Menu`.
- **Tests:**
  - Tabs and Menu keyboard behaviour;
  - the skeleton delay;
  - the Footer refused and unsure notes (unsure hides the primary and shows Refresh);
  - the Dialog sizes and the S/M/L mapping of `Modal` widths;
  - the toast action and 6 s timeout;
  - the Table row keyboard activation.
- **Done when:** Components.dc.html and States.dc.html match on `/__kit`. Every existing
  screen still passes its tests.

### R4 Shell

Artboard: Components "Sidebar" and "Top bar", and any screen artboard's shell.
- **Sidebar:**
  - 216 px, `--color-bg-subtle`, the brand row ("BSS Admin"), section labels in micro
    caps, text-only items 28 px high;
  - the active item is white with a hairline;
  - counts in mono, and the failed badge on Billing runs (logic unchanged);
  - the footer has the avatar initials, name and role.
- **Top bar:** 48 px; breadcrumbs with ink-3 parents and an ink-1 current crumb; the
  business-date chip per D6; the avatar menu.
- `App.test.tsx` and `session.test.tsx` keep their link and menu assertions.
- **Tests:** chip drift states (same day, behind, ahead) with a fixed system time; the
  avatar opens the operator menu.

### R5 Subscribers list

Artboard: `Subscribers.dc.html` (the 1280 view; the inspector waits for R15).
- **PageHeader:** "Subscribers" with the count; the primary is New subscriber.
- **Filter row:**
  - search 300 px with the icon; `/` focuses it unless an input has focus;
  - status and mode as Segmented with counts (the existing counts);
  - Reset filters only when a filter is set.
- **Columns:**
  1. Name (body-strong, link);
  2. Account (`Id` BA);
  3. Status;
  4. Mode;
  5. Products (right);
  6. Cycle day (right; "—" for prepaid);
  7. Balance EUR (right, last), with the "Over limit" Flag when the account has a credit
     limit and `balanceCents < -creditLimitCents`. That is the same rule as the internal
     `exceedsCreditLimit`, and it is informational only;
  8. Actions `⋯`: Open account, Record payment (opens the existing dialog). Only actions
     that exist today.
- The negative-balance chip and the "negative" count presentation become the D5 rules. If
  a negative filter exists, keep it as a Segmented option.
- The no-results state names the query and filters.
- **Tests:** the flag shows only over the limit; `/` focuses the search; the row menu opens
  the existing payment dialog; the no-results copy.

### R6 Subscriber card: header, money strip, tabs

Artboard: `SubscriberCard.dc.html` (top half). Spec: D12 items 1–3.
- Move the existing panels into tabs unchanged, except for restyling. Overview holds the
  products panel as it is today until R7.
- Tab state in the URL hash. An unknown hash falls back to Overview.
- **Every existing marker stays.** New: `ui.subscriber.tabs`, `ui.subscriber.moneyStrip`,
  `ui.subscriber.moreActions`.
- Tests that find panels on the card now first click their tab. Adjust with
  `TEST-CHANGE:` (structure changed by design); the assertions themselves stay.
- **Tests:**
  - each tab renders its panel;
  - the prepaid strip equation figures come from the loaded balance;
  - the postpaid strip shows the over-limit line only over the limit;
  - the `⋯` menu actions open the existing dialogs;
  - a hash deep-link opens the tab.

### R7 Subscriber card: Overview

Artboard: `SubscriberCard.dc.html` (Overview) and `SubscriberCard-1600`. Spec: D12 Overview, D10 order steps.
- **Products table:**
  - columns Product (name + code), Number, Status, Since (startDate), Price EUR (rate),
    and actions `⋯` with the existing product actions;
  - a pending change is an inline sub-row with an info Note style and Undo/Cancel change
    (the existing calls);
  - "Show N terminated" toggle.
- **In progress panel:** the existing order watch, restyled as steps from D10, with "Open
  order" linking to the Orders dialog/route as now.
- **Recent activity rail:** the last 8 transactions from the already-loaded transaction
  list; columns what/ref, amount (signed), date, balance after; "All transactions"
  switches to the tab. The prepaid footnote reads "Holds change Reserved, not Ledger."
- **Tests:**
  - the sub-row for a pending change, and Undo still sends the same request (existing
    assertion);
  - the rail shows at most 8 rows with balanceAfter;
  - the terminated toggle.

### R8 Billing runs

Artboard: `BillingRuns.dc.html`.
- **Header:** "Billing runs", the meta "Nightly at 00:30 UTC+3" from the context. Actions:
  Catch up to date… (secondary), and "Retry N failed" (primary, only when the latest run
  failed; the existing retry).
- **Latest run band:** date, StatusPill (run), stats (accounts, completed, failed, charged,
  duration), a progress bar while running, the failure summary (D10), and the next run.
- **Outcomes** for the selected run (default: the latest):
  - a Segmented filter Failed / Completed / All, defaulting to Failed when there are
    failures;
  - a dense table: Account (Id), Reason, Status (runOutcome);
  - an info note with the retry explanation.
- **History:** the last 10 runs, compact table; selecting a row selects the run for
  Outcomes. This replaces the run dialog, but its marker moves to the outcomes section.
- Polling and the give-up note stay as they are.
- **Tests:** "Retry N failed" appears only for a failed latest run; selecting a history row
  loads that run's outcomes (the existing request); the Failed default.

### R9 Product catalog

Artboard: `Catalog.dc.html`.
- **Offerings tab:**
  - rows grouped by lifecycle (In design → Active → Retired), each with a group header row
    (label, count, hint);
  - columns Offering (name + code), Lifecycle, Recurrence, Price EUR (rate), Active
    products (D10), Updated (TMF620 `lastUpdate`, date only);
  - a search field, and a Segmented filter on lifecycle with counts;
  - the note about the one-way lifecycle.
- **The offering detail dialog** keeps its function and becomes the artboard's detail
  layout:
  - a lifecycle stepper, price, actions Activate… / Retire… / Change price…;
  - a KeyValue block, and price history from the TMF620 prices already loaded.
- The other tabs (Specifications, Categories, Catalogs, Import/export) get the Tabs
  primitive, tables and states. No layout change beyond that.
- **Tests:** grouping order and group counts; the active-products count from one list
  request (pin the query string); the stepper reflects the lifecycle.

### R10 Add product dialog

Artboard: `AddProduct.dc.html`.
- Size L.
- **Left column:** the offering list as selectable rows (name, code, price/unit, note);
  the number picker; the start date.
- **Right column:** the first-charge preview from the server, as it is today (price,
  period, proration, "then … from …"), with Required now / Available / Shortfall and
  "Preview from the server · HH:MM:SS".
- **Shortfall (immediate prepaid):**
  - a warning Note with the artboard copy, using the server's numbers;
  - "Add <offering>" is disabled;
  - the primary is "Top up <shortfall> EUR…", which opens the existing top-up handoff.
- A future start keeps today's hint and enabled Add.
- Save draft keeps its place and markers.
- **Tests:** in the shortfall state the primary is Top up and Add is disabled; the label
  states the offering; the existing body pins are untouched.

### R11 Money dialogs: transfer, top-up, adjust, hold, record payment

Artboards: `TransferBalance.dc.html`, `States.dc.html` (Record payment).
- **Transfer:**
  - steps 1–4 with "Step n of 4";
  - step 1: recipient by number, the found-account card with eligibility exactly as the
    backend rules (show the refusal reason the server or the existing code gives);
  - step 2: amount with an "Up to <available>" help text and a before/after preview from
    loaded figures;
  - step 3: the confirmation summary, a warning Note, the acknowledgement checkbox (D7);
  - step 4: the watch per D9 and D10.
- **Top-up, Adjust, Hold and Record payment:**
  - size M, MoneyField;
  - "Balance after" where the dialog already knows the balance;
  - outcome labels ("Top up 20.00 EUR", "Record 42.10 EUR");
  - busy labels.
- Idempotency-Key handling and bodies are unchanged; their tests must pass untouched.
- **Tests:** the checkbox gates the transfer primary and Enter does not submit on step 3;
  the outcome labels; the unsure state shows Refresh and no retry.

### R12 Sweep: Products, Orders, Numbers, Parties, subscriber forms

- PageHeader, filter row and Table with StatusPill kinds, `Id`, Amount and row `⋯` menus.
- Detail dialogs use Dialog, KeyValue and Tabs where they had sub-sections.
- Order dialog state chips use the `order` and `orderItem` kinds.
- New/Edit subscriber forms use Field primitives.
- No new components (if one seems needed, add it to `ui/` with tests and record why).
- **Tests:** status kinds per screen; any renamed labels.

### R13 Sweep: Prepaid commands, Accounts, Bills, Payments, Balances, Billing settings

Same as R12. Use bill kinds per D4, bucket and balanceAction kinds on Balances, and the
command kind with "Failed (technical)" on Prepaid commands.

### R14 Sweep: Integrations, Users, sign-in, change password, not found; cleanup; dark

- Restyle the remaining screens. Sign-in is a centred 400 px form on `--color-bg-subtle`.
- **Cleanup:**
  - delete `legacy-tokens.css`, the `Pill`/`Modal`/`PageTitle` aliases and every remaining
    inline style except dynamic values;
  - empty the allow-list from R1;
  - grep proves no old token names remain.
- **Dark theme:** enable dark per D3 and add the Theme menu item.
- **Tests:**
  - the theme choice is applied, persisted, and survives a storage exception;
  - the hex/px test passes with an empty allow-list.
- **Shots:** take each key screen in dark too (`.shots/<name>-<width>-dark.png`).

### R15 Inspector ≥ 1536 px

Artboards: `Subscribers-1600`, `Catalog-1600`.
- **Subscribers:** at ≥ 1536 px a row click selects the row and opens a 400 px inspector
  instead of navigating. It shows:
  - the name, Id, status and mode;
  - balance with the over-limit line;
  - party; cycle and next bill;
  - the last bill and the last payment (one request each, only for the selected account,
    using existing loaders);
  - products summary and open orders;
  - actions Open account (primary) and Record payment.
- **Catalog:** the offering detail renders in the inspector instead of a dialog, with the
  same content and actions.
- Escape closes the inspector. Enter on a selected row opens the account.
- **Tests** (mock `matchMedia`):
  - below 1536 a click navigates (unchanged);
  - above, a click selects and the inspector shows;
  - the inspector's loads happen only for the selected row.

### R16 Visual QA and release evidence

- Run `make web-shots` (light and dark). Compare every key screen with its artboard and
  fix the differences.
- Run `make web-e2e`. Update `frontend/e2e/smoke.spec.ts` only where a selector changed by
  design (`TEST-CHANGE:`).
- Write `docs/ui-redesign-release.md`: the test counts (backend unchanged, frontend in
  both zones), the e2e result, the list of shots reviewed, and every D10 deviation as
  built.
- Update CLAUDE.md "Current state" (one short paragraph).
