# Handoff: BSS Admin Console (Telecom Billing)

## Overview
Desktop admin panel for a telecom BSS / billing system, used by billing operators. Data model follows TM Forum SID (Customer, BillingAccount, ProductOffering, ProductSpecification, Product, AppliedCustomerBillingRate, BillRun). Four screens + two modals:

1. Subscribers list
2. Subscriber card (billing account, active products, charge history, transaction history)
3. Product catalog
4. Billing runs
5. Modal: Add product (with first-charge proration preview)
6. Modal: New product offering

Light theme only. Minimum width 1280px, desktop only, no responsive behaviour.

## About the Design Files
The files in this bundle are **design references created in HTML**: prototypes that show the intended look and behaviour. They are not production code to copy. Your job is to **recreate these designs in the target codebase's existing environment** (React, Vue, etc.) using its own patterns, component library and data layer. If no environment exists yet, pick the most suitable framework (e.g. React + TypeScript + a table library) and build it there.

`BSS Admin.dc.html` opens directly in a browser (it needs `support.js` next to it). It has a markup template with inline styles, plus a `class Component` logic block at the bottom that holds the mock data, formatting helpers and the proration calculation. Treat that logic as a behavioural spec.

## Fidelity
**High-fidelity.** Final colours, typography, spacing, copy and interactions. Recreate it pixel-accurately, mapping values onto the codebase's design tokens wherever equivalents exist.

---

## Global Layout
- Root: `display:flex`, `min-width:1280px`, `min-height:100vh`, background `#f5f6f8`, text `#1c2127`, font **Golos Text** 13px / 1.4.
- **Sidebar**: 212px wide, white, right border `1px #e2e5e9`, sticky, full height.
  - Brand row, 52px high, padding 0 16px, bottom border: 22×22 black square (radius 4) with a white mono "B", the label "BSS Console" (600, 14px), and a "PROD" environment tag (mono 10px, 1px `#d0d5dc` border, radius 3, colour `#667085`).
  - Nav section label "Operations": 11px uppercase, letter-spacing .05em, `#8a93a0`.
  - Nav items: padding 7px 10px, radius 4, hover background `#eef1f5`.
    - Active item: background `#eaf0fb`, text `#1a3f94`, weight 600.
    - Right side shows a mono count (11px `#8a93a0`). When the latest billing run has failed, the Billing runs item shows a red "failed" badge instead (`#d92d20` background, white 10.5px/600, radius 3).
  - Footer: operator name (500) and "Billing operator · i.petrova" (12px `#667085`, login in mono).
- **Top bar**: 52px, white, bottom border, sticky, padding 0 24px.
  - Breadcrumbs: parent crumbs are `#2356c7` links, the current crumb is weight 500, separators are "/" in `#c0c6ce`.
  - Right side, 12px `#667085`: "Business date **2026-09-27**" and "Time zone **UTC+3**" (values in mono, `#1c2127`).
- **Content**: padding 20px 24px 48px, vertical gap 16px.

## Shared Patterns
- **Page title**: h1 18px/600, followed by the total count in mono 12px `#667085`.
- **Panel / table container**: white, `1px #e2e5e9` border, radius 6, overflow hidden.
  - Panel header row: padding 10px 16px, bottom border, title weight 600, an optional muted subtitle at 12px (e.g. the SID entity name).
- **Table**: built on CSS grid, with fixed column widths per table (see each screen).
  - Header row: background `#f8f9fa`, 11px/500 uppercase, letter-spacing .04em, `#667085`, padding 8px 12px.
  - Body rows: bottom border `1px #eef0f2`, min-height 37px, padding 8px 12px.
  - Clickable rows: hover `#f4f7fd`, cursor pointer.
- **Numbers**: identifiers, amounts, dates and counts all use **JetBrains Mono** 12.5px with `font-variant-numeric: tabular-nums`. Amount columns are **right-aligned**.
  - Format: thousands separator ",", decimal ".", always 2 decimals (`1,250.40`).
  - Negatives use the real minus sign "−" (U+2212) and are coloured `#b42318`.
  - Currency code follows in 11px `#8a93a0`.
- **Dates**: ISO `YYYY-MM-DD`. Times: `HH:MM:SS`.
- **Status pill**: inline-flex, gap 6, padding 2px 8px, radius 3, 11.5px/500, line-height 16px, with a 6px dot.

  | Status | Text | Background | Dot |
  |---|---|---|---|
  | Active / completed | `#1f6f46` | `#e7f4ec` | `#2f9e63` |
  | Inactive | `#5b6472` | `#eef0f3` | `#9aa3af` |
  | In Design / running | `#2350b0` | `#e8effc` | `#4a7be0` |
  | Retired | `#6b6f76` | `#f0f0f1` | `#b3b7bd` |
  | failed | `#ffffff` | `#d92d20` | `#ffffff` |

  `failed` is solid red on purpose, so it stands out.
- **Buttons**:
  - Primary: 32px high, padding 0 14px, background `#2356c7`, border `#1f4fb8`, white 13px/500, radius 4, hover `#1d49ad`.
  - Secondary: white, border `#d0d5dc`, hover `#f3f4f6`.
  - Destructive outline: white, text `#b42318`, hover background `#fdf3f2` with border `#e8b4ae`.
  - Destructive solid: `#b42318`.
  - Disabled: opacity .45, `not-allowed` cursor.
- **Inputs**: 32px high, border `#d0d5dc`, radius 4, padding 0 10px. Focus: border `#2356c7` plus ring `0 0 0 3px rgba(35,86,199,.15)`. Date inputs and amount inputs use mono.
- **Segmented filter**: buttons inside a bordered group, each 30px high, 12.5px/500, with a mono count.
  - Selected: background `#eaf0fb`, text `#1a3f94`.
  - Unselected: white, text `#475467`.
- **Table footer**: background `#fbfbfc`, 12px `#667085`. "Showing X of Y" on the left, a note on the right.

### Required states (every list)
- **Loading**: skeleton rows (8) with grey bars `#eceef1` / `#f1f2f4`, sized like the real cells; numeric skeletons are right-aligned. The card and billing screens also have skeleton KPI blocks.
- **Error**: centred block (padding 40px):
  - 28px round icon "!" (background `#fdecea`, text `#b42318`);
  - title 14px/600, e.g. "Failed to load subscribers";
  - the failing API call in mono 12px `#667085` (e.g. `GET /tmf-api/customerManagement/v4/customer → 503 Service Unavailable`);
  - a "Retry" secondary button.
- **Empty**: centred title plus an explanation and an optional call to action (see copy below).
- **No results** (filters applied): "No matches" plus a "Reset filters" button.

---

## Screen 1: Subscribers
- Toolbar:
  - search input (340px), placeholder "Search by name or subscriber number";
  - status segmented filter All / Active / Inactive, with counts;
  - right side: "Negative balance: N" (N in mono `#b42318` 600).
- Table columns:
  - `150px`: Subscriber no. (mono, `#2356c7`)
  - `1fr`: Name (500, ellipsis)
  - `120px`: Status pill
  - `190px`: Balance (right-aligned + currency)
  - `160px`: Active products (right-aligned; 0 shown in `#a0a7b1`)
  - `150px`: Registered (right-aligned)
- **Negative balance**: amount in `#b42318`, weight 600, background chip `#fdecea`, padding 1px 5px, radius 3.
- Search: case-insensitive match on name, or a substring of the number (spaces stripped).
- Clicking a row opens the Subscriber card.
- Footer: "Showing X of Y" / "Amounts in billing account currency".
- Empty copy: "No subscribers yet" / "Subscribers appear after registration via CRM or import."

## Screen 2: Subscriber Card
Top: "← Back to subscribers" link.

**Header panel** (padding 16px 20px):
- Left:
  - name (h1 20px/600) with a status pill;
  - meta row, 12px `#667085`: Subscriber no., Billing account, Registered (values in mono `#1c2127`).
- Right: primary button "+ Add product".
  - Disabled when the subscriber is Inactive, with the caption "Unavailable for inactive subscribers" underneath.

**Billing account panel**:
- Header: "Billing account" on the left; "BillingAccount BA-xxxxxxxx" on the right.
- 5-column KPI grid (`1.3fr .7fr 1fr 1fr 1fr`), each cell padding 14px 20px with vertical dividers. Each cell has a label (11px uppercase `#667085`) and a value (mono 20px).
  - **Balance**: 600 weight, red when negative, with the caption "In arrears".
  - **Currency**
  - **Billing cycle day**: "Day N", with the caption "next cycle YYYY-MM-DD".
  - **Credit limit**
  - **Available incl. credit**: balance + credit limit, red when negative.

**Active products panel**:
- Header: "Active products" plus count.
- Columns:
  - `1fr`: Offering (name 500, with `CODE · Specification` in mono 11px `#8a93a0` below)
  - `140px`: Activated
  - `170px`: Next charge
  - `170px`: Price (right-aligned, with "/ mo" or "/ wk" suffix)
  - `170px`: Actions
- Row min-height 44px.
- "Deactivate" is a destructive outline button, 26px high. Clicking it swaps in an inline confirm: "Deactivate?" in red, a solid red [Yes] and a secondary [No].
- Next charge:
  - monthly: the next billing-day date;
  - weekly: start date + 7·k;
  - future-dated: the start date.
- Empty copy: "No active products" / "Add an offering from the catalog to start charging." plus a "+ Add product" button (Active subscribers only).

**Two-column grid** (gap 12):
- **Charge history** (subtitle "AppliedCustomerBillingRate").
  - Columns: `92px` Date, `1fr` Period (mono `from – to`, with the offering name 11.5px muted below), `84px` Days, `110px` Amount.
  - Days shows "N of M" in amber `#a15c00` when the charge is prorated, otherwise "—" in `#a0a7b1`.
  - Empty copy: "No charges" / "Charges appear after the first billing run."
- **Transaction history** (subtitle: currency).
  - Columns: `92px` Date, `1fr` Type (Charge / Payment / Adjustment, with a note below), `116px` Amount, `116px` Balance after.
  - Amount is signed: credits "+" in green `#1f7a4d`, debits "−" in red `#b42318`, weight 500.
  - Balance after is red when negative.
  - Empty copy: "No transactions" / "No payments or charges on this account."

## Screen 3: Product Catalog
- Title row with a primary button "+ New offering" on the right.
- Toolbar:
  - search input (300px), placeholder "Search by code or name";
  - segmented filter All / In Design / Active / Retired, with counts.
- Columns:
  - `150px`: Code (mono)
  - `1fr`: Name (500; Retired rows are muted `#8a93a0`)
  - `190px`: Specification
  - `140px`: Price, RUB (right-aligned)
  - `110px`: Period (month / week)
  - `120px`: Status
  - `200px`: Validity (mono `from – to`, or "open-ended")
- A newly created row is highlighted `#f4f7fd`.
- Footer note: "Prices include VAT".
- Empty copy: "Catalog is empty" / "Create the first product offering. It starts in In Design." plus a button.

## Screen 4: Billing Runs
- Title row:
  - "Billing runs" plus the caption "Scheduled daily at 02:00 UTC+3";
  - right side: "Cycle date" date input (max = business date) and a primary "Run manually" button. The button is disabled while a run is in progress.
- **Failure banner** (shown only when the latest run failed):
  - solid `#d92d20`, white text, radius 6, padding 10px 14px;
  - white round "!" icon;
  - bold "Latest run for DATE failed." followed by "Some accounts were not processed; charges are incomplete.";
  - white button on the right: "Re-run for DATE".
- **Latest run panel**:
  - Border `#c9d6f2` normally. When failed: background `#fef6f5`, border `#f2c4bf`.
  - Header: "LATEST RUN" label, date (mono 600), status pill, optional "manual" tag, run id on the right (mono 11.5px muted).
  - 5 KPIs (mono 16px): Start, End, Duration, Charges, Amount, RUB (600).
  - running: progress bar (6px high, track `#e3e9f6`, fill `#2356c7`) with "N of M accounts".
  - failed: error message box (white, border `#f2c4bf`, mono 12px red).
- **Run history table** (subtitle "last 14 days"):
  - Columns:
    - `130px`: Cycle date
    - `100px`: Start
    - `110px`: End
    - `120px`: Duration ("N min SS s", or "running…")
    - `130px`: Status
    - `1fr`: Note (tags "latest" / "manual", error text in red mono with ellipsis, a "Re-run" link on failed rows)
    - `130px`: Charges
    - `170px`: Amount, RUB
  - The latest row is bold, with background `#f4f7fd` (or `#fef3f2` when it failed).
  - Other failed rows: background `#fffafa`, with the amount muted.
- Empty copy: "No runs yet" / "The first scheduled run starts at 02:00. You can also run a cycle manually for a selected date."

## Modal 5: Add Product
- Overlay `rgba(20,24,31,.42)`. Dialog 720px wide, radius 8, shadow `0 20px 50px rgba(16,24,40,.25)`, 56px from the top. Closes on × or a click on the overlay.
- Header: "Add product", then a subtitle with name · account · billing day.
- **1. Offering**:
  - caption "Active offerings only" and a filter input (230px);
  - radio list, scrollable up to 236px;
  - columns: radio `28px` / name + code `1fr` / spec `170px` / price + period `150px`;
  - selected row background `#eef3fc`, radio `#2356c7`;
  - offerings the subscriber already has get an "already active" grey tag.
- **2. Start date**: date input (min = business date). Hint:
  - "today, charged on confirmation" when the date is today;
  - otherwise "charged on the start date".
- **3. First charge preview** (panel background `#fafbfc`).
  - Placeholder until an offering is picked: "Select an offering to see the calculation".
  - Left column:
    - Period
    - Billable days ("N of M (prorated)")
    - Offering price
    - Formula (`price × days / periodDays`, or "full period")
  - Right column:
    - "First charge DATE": mono 17px/600
    - "Then from DATE": full price
    - "Balance after charge": red if negative
    - If the balance would go below −creditLimit: red warning box "Balance will exceed the credit limit of X. Activation is allowed; services may be suspended."
  - Footnote explains the proration rule.
- Footer: Cancel and a primary button.
  - Primary label: "Activate and charge X RUB" for today, or "Activate from DATE" for a future date.
  - Disabled until an offering is selected.

### Proration rule (implemented in `prorate()`)
- **Monthly**:
  - `next` = the next billing-day date after the start date (the billing day is clamped to the month length);
  - `prev` = the same day one month earlier;
  - `days = next − start`, `of = next − prev`, `amount = round2(price × days / of)`;
  - the charge is prorated when `days < of`.
- **Weekly**: a full 7 days from the start date, no proration; next charge at start + 7.
- On confirm, when the start is today, the app adds a charge, a transaction and the new balance immediately.

## Modal 6: New Product Offering
- Dialog 600px wide. Two-column form grid, gap 12px 14px:
  - Code (mono, uppercased)
  - Name
  - Specification (select)
  - Price, RUB + Recurrence (month / week)
  - Valid from
  - Valid to (empty = open-ended)
- Validation, shown as a 12px red message:
  - "Code X is already in use"
  - "Price must be a positive number"
  - "End date is before start date"
- The "Create in In Design" button is disabled until Code, Name, a valid price and Valid from are all filled.
- The new offering always starts with status **In Design**.

## Toast
Fixed bottom-right (24px), background `#1c2127`, white 13px, radius 6. Auto-hides after 3.2s. Example messages: "Mobile L activated, charged 1,290.00 RUB", "Run for 2026-09-26 started".

---

## State Management
- `screen`: subs | card | catalog | billing; `subId`
- List filters: `q`, `statusF`, `catQ`, `catF`
- `loading` / `error` per fetch (the prototype fakes a 450ms load on every navigation)
- Modal state: `modal` (connect | create), `mOffer`, `mStart`, `mq`, `nf` (form fields)
- `confirmOff`: id of the product row showing the inline deactivate confirm
- Billing: `runDate`, plus runs list with live status. Poll running runs and update the progress, count and total.

### Suggested API (TMF Open APIs)
- `GET /tmf-api/customerManagement/v4/customer`
- `GET /tmf-api/accountManagement/v4/billingAccount/{id}`
- `GET /tmf-api/productInventory/v4/product?billingAccount.id=` and `PATCH` (status → terminated) for deactivation
- `POST /tmf-api/productOrderingManagement/v4/productOrder` for adding a product
- `GET /tmf-api/customerBillManagement/v4/appliedCustomerBillingRate`
- `GET|POST /tmf-api/productCatalogManagement/v4/productOffering`
- Bill runs: internal service (the prototype uses `/billing/v1/billRun`)

The proration preview should ideally come from the backend (a dry-run endpoint), so the UI shows exactly what will be charged.

## Design Tokens
- **Colours**
  - Ink `#1c2127`; secondary text `#475467`; muted `#667085`; faint `#8a93a0`; disabled number `#a0a7b1`
  - Page `#f5f6f8`; surface `#ffffff`; table header `#f8f9fa`; footer `#fbfbfc`
  - Borders `#e2e5e9`, row dividers `#eef0f2`, input border `#d0d5dc`
  - Primary `#2356c7` (hover `#1d49ad`, border `#1f4fb8`, dark text `#1a3f94`); selection backgrounds `#eaf0fb` / `#eef3fc` / `#f4f7fd`
  - Negative `#b42318`, negative chip background `#fdecea`; alert `#d92d20`; positive `#1f7a4d`; proration amber `#a15c00`
- **Type**
  - Golos Text 400/500/600/700 for UI; JetBrains Mono 400/500/600 for data
  - Sizes: 10.5, 11, 11.5, 12, 12.5, 13 (base), 14, 15, 16, 17, 18, 20
- **Radius**: 3 (pills, tags), 4 (inputs, buttons), 6 (panels), 8 (modals)
- **Spacing**: panel gap 12–16, cell padding 8×12, page padding 20×24
- **Shadows**: modal `0 20px 50px rgba(16,24,40,.25)`; toast `0 8px 24px rgba(16,24,40,.25)`

## Assets
None. No icons or images; the only glyphs are text characters ("!", "×", "←").
Fonts load from Google Fonts: Golos Text and JetBrains Mono.

## Files
- `BSS Admin.dc.html`: the full prototype (all screens, modals, mock data, logic). Open it in a browser.
  - Prop `dataState` (live / loading / empty / error) previews the list states.
  - Prop `latestRun` (completed / failed / running) previews the billing banner.
  - Prop `startScreen` sets the initial screen.
- `support.js`: runtime needed to open the prototype. Not part of the implementation.
