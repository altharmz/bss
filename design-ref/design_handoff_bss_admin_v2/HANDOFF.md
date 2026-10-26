# BSS Admin — UI redesign handoff (v2, "Ledger")

This folder replaces the visual layer of the admin console. It is a restyle and an
information-architecture change for a few screens. It is **not** a change to API calls,
routes, permissions or business rules.

## Contents

| Path | What it is |
|---|---|
| `tokens.css` | All design tokens as CSS custom properties (light + dark). Import once at the app root. |
| `artboards/Foundations.dc.html` | Type scale, colour roles, status families, number/date rules, spacing, radii, elevation, focus, table density. **Source of truth for tokens.** |
| `artboards/Components.dc.html` | Sidebar, top bar (incl. business-date drift state), page header + filters, table anatomy (default / hover / selected / focus / flagged / footer), buttons × states, fields, money strip (prepaid + postpaid), tabs, key–value, panel, inline notes, pills/tags/badges, menu, toast. |
| `artboards/States.dc.html` | The four list states and the four write states (Record payment dialog). |
| `artboards/Subscribers.dc.html` | Subscribers list. Prop `wide` = inspector shown (≥ 1536 px). |
| `artboards/SubscriberCard.dc.html` | Subscriber card, prepaid account. New IA (see below). Prop `wide` = wider activity rail. |
| `artboards/Catalog.dc.html` | Product catalog grouped by lifecycle. Prop `wide` = inspector. |
| `artboards/BillingRuns.dc.html` | Billing runs with a failed latest run. Layout reflows by width, no prop. |
| `artboards/*-1600.dc.html` | Thin wrappers that render the screen above at 1600 px. |
| `artboards/AddProduct.dc.html` | Add product dialog (prepaid, with shortfall) over the card. |
| `artboards/TransferBalance.dc.html` | Transfer balance, 4 steps side by side. |
| `artboards/canvas.json` | Canvas layout and the designer's rationale notes (`notes.rB`, `notes.rD`). |

## How to read a `.dc.html` artboard

- The **inline `style="…"` values are the spec**: exact sizes, colours, paddings. Map each hex
  to its token in `tokens.css`; do not copy hexes into components.
- `<sc-for>` / `<sc-if>` / `{{ … }}` and the `renderVals()` script are the mock's templating.
  The arrays in `renderVals()` are **sample data only**; the data shape hints at props.
- `support.js`, `<x-dc>`, `<helmet>` and `<dc-import>` are the design tool's runtime. Ignore them.
- Rationale per screen: `canvas.json` → `notes.rB` (screens) and `notes.rD` (dialogs).

## Global rules (apply everywhere)

1. **Type:** only the 9 type tokens. No other font sizes. Remove every 10.5 / 11.5 / 12.5 px value.
2. **Numbers:** mono, tabular, 2 decimals, `1,250.40`; minus is U+2212 and uses `--color-negative`;
   currency code 11 px `--color-ink-3` after the figure (omit it when the column header names the
   currency); explicit `+` only in movement lists; zero is `0.00`, `—` means "does not apply".
3. **Dates/times:** `YYYY-MM-DD`, `HH:MM:SS`, business time zone. Relative time only as a secondary hint.
4. **IDs:** prefix (`BA-`, `ORD-`, `CMD-`, `BL-`) in ink-3, body in ink-1, mono, click to copy.
5. **Tables sit on the page**, not inside a bordered card. Header 32 px on `--color-bg-subtle`,
   rows 36 px (32 px compact), hairline between rows, no zebra. First/last cell padding 24 px.
   Hover `--color-bg-hover`; selected `--color-bg-selected` + `inset 2px 0 0 var(--color-primary)`.
   Row actions: one `⋯` menu (28 px), visible on hover/focus.
6. **One primary button per view.** It is the next useful action, labelled with the outcome
   ("Transfer 5.00 EUR", "Record 42.10 EUR", "Top up 2.56 EUR…"), never "OK".
7. **Status pills:** use the 8 families below; glyph shape + label, colour confirms. Never colour alone.
8. **Focus:** `--focus-ring` on `:focus-visible` for buttons, links, rows, tabs; inputs get primary
   border + `--focus-halo`. Never `outline: none` without a replacement.
9. **Shadows** only on floating layers (menus, toasts, dialogs). Panels use a border, no shadow.
10. **No icons on nav items or labels.** Icons only for: search, calendar, close, copy, `⋯`, notes.

### Status families

| Family | Glyph | Colour (fg / bg) | Statuses |
|---|---|---|---|
| Live | filled dot | positive / positive-tint | account Active, offering Active, product active, order completed, run completed, command succeeded |
| Moving | half-filled ring | info / info-tint | order inProgress, run running, command running |
| Queued | ring | info / info-tint | order acknowledged, command queued |
| Pending | ring | warning / warning-tint | product pendingTerminate |
| Paused | pause bars | warning / warning-tint | account Suspended, product suspended |
| Failed | diamond | negative / negative-tint | order failed, run failed, command failed |
| Draft | dashed ring | ink-2 / bg-sunken | offering In Design, product created, order draft |
| Ended | dash, outlined pill | ink-3 / bg + border-strong | account Closed, offering Retired, product terminated / cancelled, order cancelled |

Display labels are sentence case (`pendingTerminate` → "Pending terminate"). Build one
`<StatusPill status kind>` component with a single mapping table; delete the ad-hoc colour extensions.

## Screen-specific changes

- **Shell:** sidebar 216 px, text-only, section labels in micro caps; active item = white + hairline.
  Billing runs shows a red `failed` badge while the latest run is failed. Top bar: breadcrumbs left;
  business-date chip right, which turns warning-toned and states the difference when the business
  date ≠ wall-clock date.
- **Subscribers:** search first (`/` focuses it), status and mode as segmented controls with counts,
  balance is the last column, an "Over limit" flag when |balance| > credit limit. At ≥ 1536 px,
  selecting a row opens a 400 px inspector instead of navigating.
- **Subscriber card (new IA):**
  1. Header: name, status pill, mode tag; account actions (Add product, Edit, `⋯` with Record payment / Close).
  2. Money strip: prepaid = `Ledger − Reserved = Available`, Paid through, Shortfall, then
     Top up (primary) / Adjust / Hold / Transfer. Postpaid = Balance, Credit limit (+ over-limit
     flag), Cycle day / next bill, Record payment (primary).
  3. Tabs: Overview · Products · Orders · Charges · Transactions · Bills · Payments ·
     Balance actions · Commands. Overview = products table (pending change as an inline sub-row),
     "In progress" order panel with steps, and a "Recent activity" rail (last 6–8 movements with
     balance after). Rail sits beside the main column from 1280 px; wider at 1600 px.
- **Product catalog:** rows grouped by lifecycle (In design → Active → Retired) with group header
  rows; "In use" count column. At ≥ 1536 px the detail dialog becomes an inspector with a lifecycle
  stepper, price, Activate… / Change price…, price history.
- **Billing runs:** latest run band (date, status, counts, charged, duration, progress bar,
  failure summary, next run). Below: Outcomes for the selected run (Failed filter first, per-row
  Retry) and History. Page primary = "Retry N failed" when the latest run failed.

## Dialogs

Sizes S 400 (confirm), M 560 (form), L 760 (form + preview). Header: title, context, close.
Footer on `--color-bg-subtle`: state/reason text left, actions right, primary last.
Write states, for every write dialog:

| State | Behaviour |
|---|---|
| idle | Normal. |
| busy | Primary shows spinner + "-ing…" label, same width; inputs read-only; Cancel disabled. |
| refused (4xx with reason) | Server message in a negative note above the footer + code in mono; inputs kept and editable. |
| unsure (network / 5xx / timeout) | Warning note "Unsure whether it was saved", request id; **no retry button**; primary = Refresh (re-read the resource). |

Final writes (balance transfer) get a confirmation step with a summary, a warning note and an
acknowledgement checkbox; the primary is disabled until it is ticked, and Enter does not submit.
Transfer step 4 polls the prepaid command every 2 s; after 60 s it shows "Still running" and
points to Prepaid commands.

## Suggested order of work

1. Add `tokens.css` + fonts; replace hard-coded values in existing primitives with tokens.
2. Rebuild primitives: Button, Field (text/select/date/money), Segmented, Pill/Tag/Badge, Table
   (+ list states), Tabs, Menu, Toast, Note, Dialog (+ write states), MoneyStrip, KeyValue.
3. Shell (sidebar, top bar with business-date chip).
4. Subscribers → Subscriber card → Billing runs → Product catalog.
5. Add product and Transfer balance dialogs; then sweep the remaining 14 screens with the same
   primitives (they should need no new components).
6. Inspector at ≥ 1536 px last.

Check each screen at 1280 and 1600 px against its artboard. Keep existing tests green; add tests for
number formatting (U+2212, 2 decimals) and the StatusPill mapping.

## Open questions (verify against the backend before copying the copy)

- Prepaid first-charge proration: the mock charges Mobile L for 2026-10-26 → 2026-11-14,
  20 / 31 days = 16.06 EUR. Use the server's preview, not client math.
- Shortfall behaviour: the mock assumes the order is refused until the balance covers the first
  charge, so "Add" is disabled and "Top up" is primary. Adjust if orders wait instead.
- Transfer eligibility ("only active prepaid accounts") and the one-way offering lifecycle are assumed.
- Sample rows, counts, IDs and failure reasons in the artboards are invented for realism.
