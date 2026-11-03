# UI redesign "Ledger" v2: decisions and slice specs (final, 2026-10-26)

The handoff is in `design-ref/design_handoff_bss_admin_v2/` (read-only). This file records
**every** decision and specifies each slice. **Where this file and the handoff disagree, this
file wins.** Nothing is left open: a worker never decides product questions. If reality
contradicts this file, follow "When the spec is wrong".

- Tracker: the task list of epic #43 (one issue per slice).

## Rules for every slice

1. **Visual and layout only.**
   - Do not add, remove or change any HTTP request: not its path, query string, body or
     headers (including `Idempotency-Key`), nor when it is sent.
   - Polling intervals, routes, permissions and business rules stay too.
   - `tests/test_ui_requests.py` and everything under `app/` stay untouched.
2. **Read first:** CLAUDE.md, then from this file "Rules", "When the spec is wrong",
   "Decisions" and your slice section. Then [patterns.md](patterns.md), the handoff's
   `HANDOFF.md`, and the artboards your slice names.
3. **Artboards are the spec for looks.** Their inline `style` values are exact.
   - Map every value to a token in `frontend/src/ui/tokens.css`.
   - No colour literal (hex, `rgb()`, `hsl()`) outside `tokens.css`. No `px` font size
     anywhere. Text is set only through the `--type-*` tokens (the `font` shorthand), plus
     `--tracking-*` for letter-spacing.
   - The artboards' `renderVals()` data is sample data. Wording comes from this file or the
     artboard. Where the artboard shows data that D11 drops, leave it out.
4. **CSS lives in CSS files.** Keep the existing split: `ui/ui.css` (primitives),
   `app/shell.css` (shell), `screens/screens.css` (screens). Every file a slice touches loses
   its `style={{…}}` props. The only exception is a value computed at runtime (a grid
   template, a progress width), passed as a CSS custom property:
   `style={{ '--progress': '40%' }}`.
5. **Test contracts:**
   - Every `data-ui` marker stays on an element with the same meaning. New visible parts get
     the new markers named in the slice, and each is added to
     `docs/progress/scope.yaml` (`area: ui, stage: 5`).
   - Accessible names stay unless this file renames them.
   - An existing test changed because a label, class or structure changed **by this design**
     gets a `TEST-CHANGE: <reason>` line in the commit message.
   - Never delete or weaken an assertion about a request, a state, a refusal or a value.
6. **Every slice adds tests that fail without it.** The tests each slice needs are listed in
   its section.
7. **Look at it.** From slice 1 on:
   - Run `make web-shots` and open `frontend/.shots/<page>-1280.png` and `-1600.png` for
     every page the slice touches.
   - Compare them with the artboard and fix visible differences.
   - Nothing may scroll horizontally at 1280 px.
8. **Docs, in the same commit:**
   - `docs/ui.md` (the entry of each touched screen);
   - `docs/patterns.md` (when a primitive's API or a screen shape changes);
   - `docs/progress/scope.yaml` (new markers);
   - one line under "Ledger v2" in `docs/decisions/frontend.md` (built, plus any recorded
     deviation);
   - the slice's tracker row.
9. **Toolchain.** No Claude hook protects a Codex session, so follow these by hand:
   - Node, npm and npx run only in containers (`make web-*`, or `docker compose run --rm
     --no-deps web-offline sh -c 'sh scripts/check-deps.sh && …'`).
   - Nothing under `design-ref/` is ever edited.
   - No new dependency, no upgrade, no `make web-install` unless the lockfile check says so.
   - `make web-fmt` runs before every commit.

## When the spec is wrong

| Situation | Action |
|---|---|
| The code lacks a field this file assumes | Leave the element out, add the deviation line to the decision entry, continue. |
| An existing test asserts old visuals (class, tone, label, position) | Update it, with `TEST-CHANGE:`. |
| An existing test asserts behaviour (request, refusal, state, value) and the slice would break it | The slice is wrong: keep the behaviour, adapt the layout. |
| The slice is too large for one session | Split it in the tracker into `Na`/`Nb`, each committed separately. Do not widen scope. |
| A backend/API change, a migration or a billing change looks necessary | Stop and report. Never do it. |

## Decisions

### D1 Tokens

- **`ui/tokens.css`** = the handoff's `tokens.css` byte for byte, followed by this block:

  ```css
  :root {
    --tracking-page: -0.01em;
    --tracking-micro: 0.06em;
    --tracking-figure: -0.01em;
    --row-height-dense: 28px;            /* outcomes, history, command queue */
    --color-on-primary-dim: rgba(255, 255, 255, 0.35); /* spinner track on solid buttons */
  }
  ```

  In dark mode, `--color-on-primary-dim` is `rgba(18, 19, 21, 0.35)`, added to both dark
  blocks. The artboards' grey canvas `#8A8C90` is not a token and is never used.
- **Type overrides:** a `--type-*` shorthand may be followed only by these overrides:
  - the currency code: `--type-micro` + `font-weight: 400; text-transform: none;
    letter-spacing: normal`;
  - `Flag` and `Badge`: `--type-micro` + `font-weight: 500; text-transform: none;
    letter-spacing: normal`;
  - `--type-micro` used as a label always adds `text-transform: uppercase;
    letter-spacing: var(--tracking-micro)`;
  - `--type-page` and `--type-figure` add their `--tracking-*`.
- **Row heights:**
  - `--row-height` 36: default tables.
  - `--row-height-compact` 32: the card's products table, and the outcome and history
    tables in Billing runs.
  - `--row-height-dense` 28: the Prepaid commands table.
- **Legacy aliases.** The old names live in `ui/legacy-tokens.css` until slice 14 deletes
  it. Use exactly this mapping:

| Old | New | Old | New |
|---|---|---|---|
| `--ink` | `var(--color-ink-1)` | `--sel-1` | `var(--color-primary-tint)` |
| `--text-2` | `var(--color-ink-2)` | `--sel-2` | `var(--color-bg-selected)` |
| `--muted` | `var(--color-ink-3)` | `--sel-3` | `var(--color-bg-hover)` |
| `--faint` | `var(--color-ink-3)` | `--neg` | `var(--color-negative)` |
| `--num-disabled` | `var(--color-ink-4)` | `--neg-bg` | `var(--color-negative-tint)` |
| `--crumb-sep` | `var(--color-ink-4)` | `--neg-hover-bg` | `var(--color-negative-tint)` |
| `--page` | `var(--color-bg)` | `--neg-hover-border` | `var(--color-negative)` |
| `--surface` | `var(--color-bg)` | `--alert` | `var(--color-negative)` |
| `--th-bg` | `var(--color-bg-subtle)` | `--pos` | `var(--color-positive)` |
| `--foot-bg` | `var(--color-bg-subtle)` | `--prorate` | `var(--color-warning)` |
| `--preview-bg` | `var(--color-bg-subtle)` | `--run-border` | `var(--color-border-strong)` |
| `--nav-hover` | `var(--color-bg-hover)` | `--run-failed-bg` | `var(--color-negative-tint)` |
| `--btn-secondary-hover` | `var(--color-bg-hover)` | `--run-failed-border` | `var(--color-negative)` |
| `--border` | `var(--color-border)` | `--run-failed-latest` | `var(--color-negative-tint)` |
| `--divider` | `var(--color-border)` | `--run-failed-row` | `var(--color-negative-tint)` |
| `--input-border` | `var(--color-border-strong)` | `--run-track` | `var(--color-bg-sunken)` |
| `--radio-border` | `var(--color-border-strong)` | `--skeleton-1` | `var(--color-bg-sunken)` |
| `--primary` | `var(--color-primary)` | `--skeleton-2` | `var(--color-bg-subtle)` |
| `--primary-hover` | `var(--color-primary-hover)` | `--r-tag`, `--r-ctl` | `var(--radius-sm)` |
| `--primary-border` | `var(--color-primary)` | `--r-panel` | `var(--radius-md)` |
| `--primary-ink` | `var(--color-primary)` | `--r-modal` | `var(--radius-lg)` |
| `--focus-ring` | `var(--color-primary-tint)` | `--shadow-modal` | `var(--elev-dialog)` |
| `--sidebar-w` | `var(--sidebar-width)` | `--shadow-toast` | `var(--elev-popover)` |
| `--bar-h` | `var(--topbar-height)` | `--overlay` | `var(--scrim)` |
| `--pad-page` | `var(--space-xl) var(--space-xl) var(--space-3xl)` | `--pad-cell` | `var(--space-sm) var(--space-md)` |
| `--gap-panel` | `var(--space-lg)` | | |
| `--fs-105`, `--fs-11` | `11px` | `--fs-115`, `--fs-12` | `12px` |
| `--fs-125`, `--fs-13`, `--fs-14` | `13px` | `--fs-15`, `--fs-16`, `--fs-17` | `15px` |
| `--fs-18`, `--fs-20` | `20px` | | |

  `--st-*` pill tokens are not aliased. Slice 2 replaces the pill and deletes them. Until
  then, slice 1 keeps them in `legacy-tokens.css` with these values:
  - `active`: `--color-positive` / `--color-positive-tint` / `--color-positive`
  - `inactive`: `--color-ink-2` / `--color-bg-sunken` / `--color-ink-4`
  - `design`: `--color-info` / `--color-info-tint` / `--color-info`
  - `retired`: `--color-ink-3` / `--color-bg` / `--color-border-strong`
  - `failed`: `--color-on-primary` / `--color-negative` / `--color-on-primary`

  The `--fs-*` aliases are the only place where a `px` font size may appear, and only in
  `legacy-tokens.css`.

### D2 Fonts

- Imports in `ui/base.css`: `@fontsource/golos-text` 400, 500, 600 and
  `@fontsource/jetbrains-mono` 400, 500. Remove the Golos 700 and Mono 600 imports.
- Every `font-weight: 700` becomes 600. Every mono 600 becomes 500.

### D3 Theme

- **Slices 1–13:** `tokens.css` keeps the handoff's `@media (prefers-color-scheme: dark)`
  block, but wrapped in a comment (`/* enabled in slice 14 … */`). The
  `:root[data-theme="dark"]` block is active, and nothing sets it yet.
- **Slice 14:**
  - uncomment the media block;
  - add the operator-menu item "Theme" with the options System / Light / Dark;
  - store the choice under the localStorage key `bss.theme`, with every access in
    try/catch;
  - apply it as `data-theme="light"|"dark"` on `<html>`, or remove the attribute for
    System;
  - apply it before first paint, from a 6-line inline script in `frontend/index.html` that
    reads the same key.

### D4 Status pill: one component, explicit kinds

`StatusPill({ kind, status, label? })` and the mapping table in `ui/status.ts`. Every call
site passes a `kind`, so the mapping is never guessed.

- An unknown `status` renders the Draft family with the raw value as its label.
- `label` overrides the display label.
- The default label is the "Label" column below, else sentence case of the value (split
  camelCase: `pendingTerminate` → "Pending terminate").

The families and their glyphs are as in HANDOFF "Status families":
- Live: filled dot
- Moving: half ring
- Queued: ring
- Pending: ring
- Paused: pause bars
- Failed: diamond
- Draft: dashed ring
- Ended: dash, outlined

| kind | value → family (Label if not sentence case) |
|---|---|
| `account` (TMF666, billing/party/settlement account `state`) | Active → Live · Suspended → Paused · Closed → Ended |
| `customer` (TMF629) | Created → Draft · Active → Live · Inactive → Ended |
| `party` (TMF632) | initialized → Draft · validated → Live · deceased → Ended · closed → Ended |
| `lifecycle` (TMF620 offering, specification, category `lifecycleStatus`) | In study, In design, In Design, In test → Draft ("In design" etc.) · Active, Launched → Live · Rejected → Failed · Retired, Obsolete → Ended |
| `product` (TMF637) | created → Draft · pendingActive → Queued · active → Live · pendingTerminate → Pending · suspended → Paused · terminated, cancelled → Ended |
| `order` (TMF622 order and item `state`) | draft → Draft · acknowledged, pending → Queued · inProgress, assessingCancellation, pendingCancellation → Moving · held → Paused · completed → Live · failed, partial, rejected → Failed · cancelled → Ended |
| `run` (internal) | running → Moving · completed → Live · failed → Failed |
| `runOutcome` (internal) | ok → Live ("Billed") · blocked → Pending ("Waiting") · failed → Failed |
| `command` (internal prepaid) | pending → Queued ("Queued") · completed → Live · failedTechnical → Failed ("Failed (technical)") |
| `bill` (TMF678) | new → Draft · validated → Queued ("Ready to send") · sent → Pending ("Unpaid") · partiallyPaid → Pending ("Partly paid") · onHold → Paused ("On hold") · settled → Ended |
| `onDemandBill` (TMF678) | inProgress → Moving · done → Live · rejected, terminatedWithError → Failed |
| `payment` (TMF676 payment and refund) | pendingAuthorization → Queued · authorized, captured, done → Live · failed, denied → Failed · canceled → Ended ("Cancelled") |
| `bucket` (TMF654) | active → Live · suspended → Paused · expired → Ended |
| `balanceAction` (TMF654) | created → Queued ("Accepted") · completed → Live · failed → Failed · cancelled → Ended |
| `delivery` (hub) | pending → Queued · delivered → Live · failed → Failed · dropped → Ended |
| `catalogJob` (TMF620) | Not Started → Queued · Running → Moving · Succeeded → Live · Failed → Failed |
| `health` (System panel) | ok → Live ("OK") · error → Failed ("Error") |

Existing callers that pass a custom `label` keep it, for example Balances'
`statusLabel(action)` and the card's product status note. "Built in" on Billing settings is
not a status: it becomes a `Tag`. Integrations' `DELIVERY_PILL` table is deleted (kind
`delivery`). System health passes `status={ok ? 'ok' : 'error'}`.

### D5 Numbers, ids, dates

**`Amount`:**
- Mono and tabular.
- Negative: U+2212 and `--color-negative`.
- Currency per the D1 type override, in `--color-ink-3`, with a 4 px gap.
- An explicit `+` only with `signed` (transactions, payments, balance movements, the
  activity rail).
- Positive amounts are never green.
- The `chip` and `strong` props are removed. Callers that used `strong` use the
  `--type-body-strong` weight through a wrapping class.

**`Id`** renders a display id:
- the prefix in `--color-ink-3`, the body in `--color-ink-1`, mono `--type-code`;
- it is a `<button>` with aria-label "Copy <full id>": a click copies the full UUID with
  `navigator.clipboard.writeText` and toasts "Copied";
- if the clipboard throws, the toast says "Copy failed".

Prefixes:

| Object | Prefix |
|---|---|
| Billing account | `BA-` |
| Order | `ORD-` |
| Bill | `BL-` |
| Prepaid command | `CMD-` |
| Payment | `PAY-` |
| Refund | `RF-` |
| Product | `PRD-` |
| Balance action | `BAL-` |

The body is the existing `shortId()` (the first 8 hex digits, upper case). The customer's
"Subscriber no." keeps its current text format, with no prefix. Anywhere a screen now prints
`accountLabel(id)` or `shortId(id)` for one of these objects, it renders `<Id>`.

**Dates:** dates and times keep `format/dates.ts`. A relative hint ("in 19 days") is
computed from `context.businessDate` only.

### D6 Shell

**Sidebar** (216 px, `--color-bg-subtle`, right hairline):
- **Brand row**, 28 px: an 18 px ink-1 square with a mono "B", then "BSS Admin". The
  environment tag moves next to it as a `Tag` with the `context.environment` value,
  upper-cased.
- **Sections:** `--type-micro` labels.
- **Items:** 28 px high, ink-2 text, radius sm, hover `--color-bg-hover`. The active item
  is `--color-bg` with a 1 px `--color-border` ring, ink-1 text and weight 500.
- Counts in mono `--type-code` ink-3. The "failed" badge has the negative fill and white
  text, with the same logic as now.

**Footer** (top hairline):
- a 24 px round avatar showing initials (see below), the operator's name, and the role
  underneath (`--type-label` ink-3);
- the whole footer is the trigger for the existing operator menu (`details/summary` stays,
  with the same items and markers);
- initials are the first letters of the first two words of the display name, else the
  first two characters of the login, upper case.

**Top bar** (48 px, `--color-bg`, bottom hairline):
- Breadcrumbs on the left: parents are ink-3 links with no underline, separators are ink-4
  "/", and the current crumb is ink-1 weight 500.
- The business-date chip on the right; nothing else.

**Business-date chip:**
- Shows `context.businessDate` and `UTC` + `context.utcOffset` without a leading zero
  ("UTC+3"; "UTC+5:30" when the minutes are not zero).
- **Drift** compares the business date with the calendar date of `context.now` in
  `context.timeZone`, using `Intl.DateTimeFormat`. No browser clock.
- When they differ, the chip takes the warning style and adds a third segment:
  - "1 day behind the wall clock" / "N days behind the wall clock" when the business date
    is earlier;
  - "… ahead of the wall clock" when it is later.

### D7 Dialogs and write states

**`Dialog`** (`ui/kit.tsx`):
- props: `size: 'S' | 'M' | 'L'` (400 / 560 / 760 px), `title`, `context` (one line under
  the title, ink-3), `step` (`{ n, of }`, rendered "Step n of N" after the title), `footer`,
  `ui`, `onClose`;
- header padding 16/20, title `--type-section`, a 28 px quiet close button with an `×` icon;
- the body is padded 20;
- the footer has a top hairline on `--color-bg-subtle`, padding 12/20, a left slot for state
  text, and actions on the right;
- `Modal` stays as a wrapper until slice 14: width ≤ 440 → S, ≤ 620 → M, else L.

**Write states** stay in `screens/FinancialForms.tsx` (`useWrite`, `Footer`), which gains:
- **busy:** the primary `Button` gets `busy`. It keeps its label, shows a 12 px spinner
  before it, keeps its width and is disabled. Cancel and the inputs are disabled. A dialog
  may pass `busyLabel`; slices 10 and 11 list where.
- **refused** (`write.problem`): a negative `Note` directly above the footer. The lead is
  "Refused.", then the server message. If the API error has a code, the code follows in
  mono ink-3.
- **unsure** (`write.unsure`): a warning `Note` above the footer reading "Unsure whether it
  was saved. The request was sent but no answer came back. Refresh to read it back before
  trying again." It is followed by the failed call (`ApiError` message) in mono ink-3. The
  footer then shows only Cancel and a primary **Refresh**; the dialog's own primary is
  hidden.

**Final writes:** the transfer's confirm step gains a checkbox, "I confirmed the amount and
the recipient's number with the subscriber". The primary is disabled until it is ticked,
and Enter does not submit on that step.

### D8 Toast

- Bottom-left, 24 px from the edges. Ink-1 background, `--color-bg` text, radius md,
  `--elev-popover`, padding 10/12/10/14.
- Shown for 6 s. One at a time.
- `useToast()` returns `(message, action?: { label: string; onClick: () => void })`; an
  action renders as a `--color-primary-tint`-coloured text button at the right.
- Success only, as now.

### D9 Polling

- Keep `POLL_MS` = 5 s and `POLL_CAP_MS` = 3 min (`screens/watched.ts`) and the billing
  runs' 5 s / 3 min.
- The copy shows "Checking every 5 s · last check HH:MM:SS".
- At the cap: "Still running. It continues in Prepaid commands." with a link to
  `/prepaid-commands?account=<id>`. For orders the link text is "Orders" and it goes to
  `/orders?account=<id>`.

### D10 Layout

- Minimum width 1280 px. The page background is `--color-bg`. The content padding is
  24 px at the sides and 20 px at the top.
- **Tables sit on the page:** no panel border, no radius, no card. The header row has a top
  hairline.
- **Panels** (hairline border, radius md, 44 px header) are used only for:
  - the card's "In progress" box;
  - the billing-run band;
  - the inspector;
  - forms grouped inside a dialog.
- The inspector (slice 15) shows at a viewport width ≥ 1536 px, through
  `useMediaQuery('(min-width: 1536px)')` in `ui/useMediaQuery.ts`.

### D11 Artboard content that is dropped or replaced

| Artboard | Built as |
|---|---|
| Subscribers "Export CSV" | Not built. |
| Subscribers row `⋯` menu | Not built: the whole row opens the account, as now. |
| Card top-bar avatar and Account menu | Not built. The operator menu stays in the sidebar footer (D6). |
| Card "Customer since …" | Not shown (TMF629/666 have no creation date). |
| Card Products tab, "Show 1 terminated" | Not built: the card loads only its current products. They are on Overview. |
| Card Orders tab | Not built: the card has no order list. The meta line keeps the existing "Orders" link to `/orders?account=<id>`. |
| Catalog "In use" column | Not built (it would need a new request). |
| Catalog "Created … by a.lepp" | Not shown. "Updated" shows TMF620 `lastUpdate` (date) when present, else "—". |
| Billing runs per-row Retry | Not built. Retry is per run; it already retries only the failed accounts. |
| Billing runs reason summary "4 × rating timeout" | "N failed · M waiting", plus `run.error` when `failureKind === 'run'`. |
| Add product "Reserved for you until …" | Not shown (claims have no expiry). |
| Unsure "req 7f3a91c2" | The failed call text instead (D7). |
| Transfer step 4 inside the dialog | Not built: an accepted transfer closes the dialog and the card's "In progress" box follows it (D9), as now. The dialog has 3 steps: Recipient, Amount, Confirm. |
| Order steps with timestamps | The "In progress" box shows command phases (slice 7), not TMF622 timestamps. |
| Display ids like `ORD-26-10-00418` | `ORD-` + the 8-hex short id (D5). |

### D12 Subscriber card

1. **Header** (no panel):
   - `--type-page` name, then `StatusPill kind=account`, then the mode `Tag` (PREPAID /
     POSTPAID).
   - Actions on the right: Add product (secondary, label "Add product", no "+"), Edit
     (secondary), and a `⋯` Menu "More actions" (`data-ui="ui.subscriber.moreActions"`).
   - **Menu items:** Record payment (postpaid only; for prepaid it stays a strip action),
     Delete… (only when `deletable`, negative text, keeps marker `ui.subscriber.delete`).
     Refund and on-demand bill keep their current places (Account tab, Bills tab).
   - The meta line (`--type-label` ink-3, items separated by "·"):
     - `<Id>` of the billing account;
     - "Subscriber no." + value;
     - "Customer" + `StatusPill kind=customer`;
     - the financial account link (keeps `ui.subscriber.financialAccount`) when present;
     - the "Orders" link (keeps `ui.subscriber.orders`);
     - for prepaid, "Charged daily at <billingRunAt>".
   - The "Unavailable for … accounts" caption stays under the actions when Add product is
     disabled.
2. **Money strip** (`MoneyStrip`, `data-ui="ui.subscriber.moneyStrip"`), full width,
   `--color-bg-subtle`, hairlines top and bottom:
   - **Prepaid:**
     - Ledger − Reserved = Available (`--type-figure`; Available ink-1 with an ink-1 label,
       the others ink-2), keeping markers `ui.subscriber.prepaid.reserved` and
       `.available`;
     - a vertical hairline;
     - Paid through (date + "in N days", marker `.paidThrough`);
     - Shortfall ("—" + "none", or the amount in negative + "renewal needs X, available Y";
       marker `.shortfall`; the suspension causes line and the "Commands" link
       `ui.subscriber.prepaid.commands` follow under it);
     - actions: Top up (primary), Adjust, Hold, Transfer (secondary).
   - **Postpaid:**
     - Balance (`--type-figure`, ink-1 label);
     - a vertical hairline;
     - Credit limit (value or "—", and "Over by X" in warning when over the limit);
     - Cycle day ("Day N" + "next bill <date>");
     - Due on bills (the outstanding amount + "N overdue · X" in negative, or "N on hold");
     - action: Record payment (primary).
   - The prepaid balance error shows as a negative `Note` under the strip.
3. **Tabs** (`data-ui="ui.subscriber.tabs"`), with the selected tab in the URL hash; an
   unknown or empty hash means Overview:
   - `#overview` **Overview**: the products table (slice 7), the "In progress" box, and the
     Recent activity rail.
   - `#bills` **Bills**: the bills panel, the record payment entry and the on-demand bill.
   - `#payments` **Payments**: the payments panel, then the refunds panel.
   - `#charges` **Charges**: the charge history.
   - `#transactions` **Transactions**: the transaction history.
   - `#balance` **Balance actions**: prepaid only.
   - `#commands` **Commands**: prepaid only; the failed-commands panel and a link to the
     Prepaid commands screen.
   - `#account` **Account**: a `KeyValue` block with currency, cycle day and next cycle,
     credit limit and "Available incl. credit" (postpaid), due on bills, and unused credit
     with the existing Refund action. The financial account link stays.

   Tab counts come from data already loaded: Bills (bills), Payments (payments + refunds
   when loaded), Charges and Transactions (rows loaded, max 20, shown as "20+" when 20). The
   Commands tab shows an info `Badge` with the failed-command count when it is > 0.
4. **Layout:**
   - Overview is a two-column grid: main `minmax(0, 1fr)` and the rail (320 px; 400 px at
     ≥ 1536 px), with a 32 px gap.
   - Other tabs are full width.

### D13 Currency (2026-10-27, GUI review B5)

Every "EUR" in this file (column headers such as "Price EUR", "Shortfall <X> EUR.",
"Top up <x> EUR", "Record <x> EUR") stands for the currency of the account concerned, or
the system currency where no account is involved. It is never a literal: amounts go
through `Amount`, and a column header names a currency only when every loaded row shares
it. Built in the GUI review slices (epic #43, G1 onward).

## Slice specs

Slices 1-6 are built (tokens, primitives, tables, shell, Subscribers list, card header and tabs): their specs were removed; see `git log`, [ui.md](ui.md) and [patterns.md](patterns.md). Remaining slices are tracked as issues in epic #43.

### Slice 7: Subscriber card: Overview

Artboard: `SubscriberCard.dc.html` and `SubscriberCard-1600.dc.html`.
1. **Products table** (`density="compact"`):
   - columns Product (name, then the offering code in mono ink-3 under the same line as a
     second span), Number (mono), Status (`StatusPill product`, with the existing note
     label), Since (the product start date), Price EUR (rate "9.90 / month", unit ink-3),
     and the actions column (`Menu` with the existing ProductActions items);
   - a pending change renders as a sub-row: an indented line on `--color-info-tint` with
     "Pending change", the change text, and the existing Undo button (markers
     `ui.subscriber.pendingChange`, `ui.subscriber.undoChange`,
     `ui.subscriber.undoDeactivation` stay);
   - the top-up product action (`ui.subscriber.topUpProduct`) and deactivate
     (`ui.subscriber.deactivate`) stay in the row menu.
2. **"In progress" box** (Panel, `data-ui="ui.subscriber.inProgress"`): it renders the
   existing watch (`PrepaidOrderWatch`, the transfer watch) as steps:
   - Accepted (done);
   - Waiting for the worker (current while polling);
   - Needs top-up (warning, when the command waits for funds, with the existing Top up
     action);
   - then Completed (done), or Failed (technical) with the existing Retry.

   It also shows the D9 copy. The box is absent when nothing is watched.
3. **Recent activity rail** (`data-ui="ui.subscriber.activity"`):
   - the heading "Recent activity" and the link button "All transactions" (selects
     `#transactions`);
   - the first 8 rows of the loaded transactions: note and type (ink-3), the amount
     `signed`, the date (mono ink-3), balanceAfter (mono, right);
   - for prepaid, the footnote "Ledger balance after each movement. Holds change Reserved,
     not Ledger.";
   - empty: "No movements yet."

Tests:
- the pending sub-row renders and Undo sends the same request (the existing assertion);
- the rail shows 8 of 20 loaded rows with balanceAfter;
- the "All transactions" link switches the tab;
- the In progress steps for each command phase.

### Slice 8: Billing runs

Artboard: `BillingRuns.dc.html`.
1. **PageHeader:**
   - title "Billing runs";
   - meta "Nightly at <billingRunAt> UTC±n";
   - actions "Catch up to date…" (secondary, existing marker `ui.billingRuns.catchUp`), and
     "Retry N failed" (primary, marker `ui.billingRuns.retry`) only when the latest run
     failed. N = `accounts.failed`, or "Retry run" when `failureKind === 'run'`.
2. **Latest run band** (Panel, `data-ui="ui.billingRuns.latest"`):
   - run date (mono), `StatusPill run`, and the stats Accounts total, Billed (`ok`), Failed,
     Waiting (`blocked`), Charged (sum, `Amount`), and Took (`finishedAt − startedAt`, as
     "4 min 12 s");
   - a progress bar while running (`(ok+failed+blocked)/total`);
   - the D11 failure summary line;
   - "Next run <date> <billingRunAt> · in Xh Ym", computed from `context.now` and the
     zone. The next run's date is the latest run date + 1;
   - the failed style (negative tint and border) when failed.
3. **Outcomes** (`data-ui="ui.billingRuns.outcomes"`, replaces the run dialog):
   - for the selected run (default the latest);
   - the existing Segmented "Account outcome" (same keys and counts) relabelled Failed /
     Waiting / Billed / All. Its default is the existing `firstFilter` (failed, else
     blocked, else all), and the requests are the dialog's (`loadRunAccounts(id, filter)`);
   - a compact table: Account (name + `Id`), Outcome (`StatusPill runOutcome`), Reason
     (`error`, or "Waits for the run of <date>" for blocked; "—" for ok), Tries (`attempts`,
     right), Finished (time);
   - an info Note under it: "Retry re-bills only the failed accounts for <date>. Billed
     accounts are not charged twice.";
   - for a selected non-latest failed run, a secondary "Retry" in the section header (same
     call and marker).
4. **History:**
   - the last runs already loaded, in a compact table: Date, Status, Trigger, Took, Failed,
     Charged;
   - a row click selects the run for Outcomes (`selected` row);
   - a failed row keeps a quiet "Retry" in its actions cell (marker
     `ui.billingRuns.retry`, as now).

Polling, catch-up and retry behaviour and requests are unchanged.

Tests:
- the primary is present only for a failed latest run, with the right label for each
  failureKind;
- selecting a history row loads that run's outcomes (the existing request string);
- the `firstFilter` default for the latest run;
- the blocked reason text;
- the next-run text with a fixed context.

The run-dialog tests move to the section (`TEST-CHANGE: run outcomes inline`).

### Slice 9: Product catalog and offering detail

Artboard: `Catalog.dc.html`.
1. **Offerings tab:**
   - PageHeader "Product catalog", count "N offerings", primary "New offering" (existing);
   - filters: search (name or code, client-side on loaded rows), and a Segmented lifecycle
     filter (All / In design / Active / Retired, with counts);
   - the table is grouped in the order In design → Active → Retired. Each group starts with
     a header row on `--color-bg-subtle`: label, count, and a hint ("Not sellable yet",
     "Sellable", "Billing only"). Empty groups are hidden;
   - columns: Offering (name + code), Lifecycle (`StatusPill lifecycle`), Recurrence
     ("Monthly", "Every 2 weeks" from the existing formatter), Price EUR (rate), and Updated
     (`lastUpdate` date or "—");
   - an info Note under the table: "Lifecycle runs one way: In design → Active → Retired.
     Retired offerings stay billable for products already on them and are hidden from Add
     product."
2. **Offering detail dialog** (size L, existing marker):
   - a lifecycle stepper with three steps (done / current / later, the current one in
     ink-1 600) and a caption under each ("In design since <date>" when known);
   - the price `--type-figure` with "/ unit";
   - actions: the existing lifecycle action ("Activate…", "Retire…") and "Change price…",
     with markers kept;
   - a `KeyValue` with recurrence, code, specification, and "valid from";
   - "Prices": a compact table of the prices the dialog already loads (from, to, amount);
   - the existing edit and delete actions move into a `⋯` Menu in the dialog header.
3. **The other tabs** (Specifications, Categories, Catalogs, Import/export) switch to
   `Tabs`, the tables and the list states, and remove their inline styles. Statuses use
   `lifecycle` / `catalogJob`.

Tests: group order, group counts and hidden empty groups; the search on code; the stepper
state for each lifecycle; the Note text; the menu holds edit and delete.

### Slice 10: Add product dialog

Artboard: `AddProduct.dc.html`. Size L, `context` = "<name> · BA-xxxx · prepaid|postpaid".
- **Two columns** (1fr / 300 px):
  - Left: the offering choice as selectable rows (radio semantics; name + code, a note,
    and the rate on the right; the selected row gets `--color-bg-selected` + an inset
    bar), then Number (the existing picker) and Start date (`DateField`, "Today (business
    date)" help).
  - Right: the existing server preview in a `--color-bg-subtle` box with "First charge"
    (`--type-figure`), price, period, proration and the "then …" line; for prepaid, a
    `KeyValue` with Required now, Available and Shortfall, and the caption "Preview from the
    server · HH:MM:SS" (the time the preview answered, in the business zone).
- **Immediate prepaid shortfall:** a warning `Note` lead "Shortfall <X> EUR." with text
  "The first charge needs <required> and <available> is available. Top up first; the
  product can be added once the balance covers it."
  - The "Add <offering name>" button is secondary and disabled.
  - The primary is "Top up <X> EUR…", the existing handoff, keeping marker
    `ui.addProduct.topUpShortfall`.
- **Otherwise:** the primary is "Add <offering name>" (busyLabel "Adding…").
- Save draft (and save draft changes) are secondary at the left of the footer actions,
  markers kept. A future-start shortfall keeps today's info hint and the enabled Add.

Tests:
- the shortfall state: Top up is primary and Add is disabled;
- the Add label includes the offering;
- the radio semantics of the offering rows;
- existing body pins untouched.

The label change is `TEST-CHANGE: outcome labels`.

### Slice 11: Money dialogs: transfer, top-up, adjust, hold, record payment

Artboards: `TransferBalance.dc.html` (steps 1–3), `States.dc.html` (Record payment).
1. **Transfer** (size M, `step` 1–3):
   - **Recipient:** the existing number lookup and its found-account result, shown as a
     bordered row with name, `StatusPill account`, `Id`, number and the mode `Tag`. When
     the existing code says the recipient cannot receive, a warning Note shows its existing
     sentence. Info text: "Only active prepaid accounts can receive a transfer." Next is
     disabled until the recipient is valid.
   - **Amount:** `MoneyField` with the help "Up to <available>, the available balance.
     Reserved money can't be moved." and a before/after `KeyValue` ("<sender> · available A
     → A−x", "<recipient> · ledger L → L+x" when the recipient's ledger is in the loaded
     lookup result; otherwise only the sender line).
   - **Confirm:** a summary block ("You are transferring", the amount in `--type-figure`,
     From and To lines with `Id`), the warning Note "This is final. A transfer can't be
     undone here. Correcting it needs a new transfer from the recipient, with their
     consent.", the D7 checkbox, and the primary "Transfer <x> EUR" (busyLabel
     "Transferring…").
   - The existing two-stage logic maps to these three steps. Recipient and Amount were one
     form, so splitting them changes no request. The bodies and Idempotency-Key are
     unchanged.
2. **Top up / Adjust / Hold** (size M):
   - `MoneyField`, and the existing fields as `Field`s;
   - a "Balance after" line from the loaded figures (ledger for top-up and adjust, available
     for hold);
   - primary labels: "Top up <x> EUR", "Apply adjustment", "Hold <x> EUR" (with no valid
     amount: "Top up", "Hold");
   - busyLabels: "Topping up…", "Applying…", "Holding…".
3. **Record payment** (size M) per States.dc.html:
   - the context line is the name and `Id`;
   - "Balance <b>" and "Balance after <b+x>";
   - `MoneyField` "Amount received";
   - the existing allocation fields as `Field`s;
   - primary "Record <x> EUR" (busyLabel "Recording…"), "Record payment" without a valid
     amount.

Tests:
- the checkbox gates the primary;
- Enter on the confirm step does not submit;
- each primary label with and without an amount;
- the Balance after values;
- the unsure state in Record payment shows Refresh and no Record button.

Existing tests clicking the old labels get `TEST-CHANGE: outcome labels`.

### Slice 12: Sweep 1: Products, Orders, Numbers, Parties, subscriber forms

Every listed screen, with these rules:
- `PageHeader` with filters;
- tables per slice 3, with `Id` for every display id (D5), `StatusPill` kinds (`product`,
  `order`, `party`, `customer`) and `Amount`;
- detail dialogs use `Dialog` (size by content: L for order details, M for the others) and
  `KeyValue` for label/value lists;
- order items and cancellations as compact tables inside the dialog;
- no inline styles left in these files;
- the New and Edit subscriber forms (`SubscriberForms.tsx`) use `Field`, `TextInput`,
  `SelectField` and `Segmented as="radio"` for the mode switch.

Tests: one per screen asserting the kind mapping of a rendered status, and the `Id`
prefixes used. The guard test from slice 1 passes with no new exceptions.

### Slice 13: Sweep 2: Prepaid commands, Accounts, Bills, Payments, Balances, Billing settings

Same rules as slice 12, with these kinds:
- `command`: the Prepaid commands table uses `density="dense"`;
- `account`: financial, party and settlement accounts;
- `bill` and `onDemandBill`: the subscriber Bills panel, Global bills and the bill dialog;
- `payment`: payments and refunds;
- `bucket` and `balanceAction`: Balances and PrepaidHistory.

The "built in" `Tag` on Billing settings. Tests as in slice 12.

### Slice 14: Sweep 3, cleanup, dark theme

1. **Restyle:**
   - Integrations and the System panel (`delivery`, `health` kinds);
   - Users;
   - Sign-in (a 400 px column centred on `--color-bg-subtle`, the form in a Panel, the
     title `--type-page`);
   - Change password (the same);
   - Not found (PageHeader "Page not found", a sentence and a link to Subscribers).
2. **Cleanup:**
   - delete `legacy-tokens.css`, `PageTitle` and `Modal` (all callers use `PageHeader` and
     `Dialog`);
   - delete every remaining inline style except D-rule 4 runtime variables;
   - extend the style guard: (d) no `var(--` name that is not defined in `tokens.css`, and
     (e) no `style={{` in `src/**/*.tsx` except `style={{ '--`.
3. **Dark theme:** per D3.
4. **`make web-shots`** also writes `<page>-1280-dark.png`, `<page>-1600-dark.png` by
   setting `localStorage['bss.theme'] = 'dark'` before the page loads.
5. **Tests:**
   - the theme choice applies and removes `data-theme` and persists;
   - a throwing `localStorage` falls back to System without crashing;
   - the guard's (d) and (e) pass.

### Slice 15: Inspector at ≥ 1536 px

1. **`useMediaQuery`** (`ui/useMediaQuery.ts`) with a `matchMedia` listener.
2. **Subscribers:** at ≥ 1536 px a row click selects the row (`selected`) and opens a 400 px
   inspector Panel at the right of the table (`data-ui="ui.subscribers.inspector"`). Enter
   or a double click opens the card, and Escape closes the inspector. Its content comes only
   from the loaded list data, with no new request:
   - name (`--type-section`), `Id`, `StatusPill account`, the mode `Tag`;
   - Balance (`--type-figure`) and the over-limit line;
   - `KeyValue`: customer, Cycle day, Credit limit, Products ("N active ·" + offering
     names with counts);
   - actions "Open account" (primary).

   Below 1536 px, a click navigates as now.
3. **Catalog:** at ≥ 1536 px a row click shows the offering detail in a 400 px inspector
   (`data-ui="ui.catalog.inspector"`) instead of the dialog. It has the same component
   body and the same requests, and the dialog-only parts (the `⋯` menu, the actions) render
   in the inspector header. Below 1536 px, the dialog shows as now.

Tests (mock `matchMedia`):
- below 1536 a click navigates or opens the dialog;
- above, it selects and shows the inspector;
- Enter opens the card;
- Escape closes;
- the inspector triggers no request on Subscribers (the stubs fail on unknown requests);
- the Catalog inspector makes the same detail request as the dialog.

### Slice 16: Visual QA, e2e, release evidence

1. `make web-shots` (light and dark). Compare every page with its artboard and fix what
   differs: spacing, type, colours, alignment, overflow.
2. `make web-e2e`. In `frontend/e2e/smoke.spec.ts`, change only selectors that changed by
   design (`TEST-CHANGE:`).
3. Write `docs/ui-redesign-release.md`:
   - the frontend test counts (UTC and New York), the backend count from the hook, and the
     e2e result;
   - the list of shots reviewed;
   - the D11 deviations as built;
   - every deviation recorded in the slice lines.
4. Update CLAUDE.md "Current state": the redesign is done, with the release evidence link.
