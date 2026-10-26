# UI redesign brief (for Claude Design)

Paste everything below the line into Claude Design. Attach:
`design-ref/design_handoff_bss_admin/BSS Admin.dc.html` + `support.js` (the first handoff)
and screenshots of the screens marked ★ (from `make up` + `make web-dev`, seeded with `make seed`).

---

## What this is

An admin console for a telecom **BSS** (billing and subscriber management) built on the
TM Forum Open APIs. Its users are **billing operators and back-office staff**: people who use it
every day for hours, look up a subscriber while on the phone, check why a bill or a balance
is what it is, place orders, record payments and top-ups, and watch the nightly billing run.
It is a working tool. Speed of reading, density and trust in the numbers come before anything
decorative.

## Where we are

The first version came from a design handoff (attached): 4 screens and 2 modals (Subscribers,
Subscriber card, Product catalog, Billing runs, Add product, New offering). Since then the app
has grown to **18 screens** and dozens of dialogs. These were built feature by feature by engineers
reusing the original primitives, with no designer involved. The result works but reads as generic and
machine-made:

- No real type scale: 12 font sizes, including 10.5 / 11.5 / 12.5 px half-steps that were
  picked one by one.
- Every screen has the same shape: page title, a white bordered panel, a table. Nothing tells you
  which screen is primary and which is secondary, or where to look first.
- A detail screen (the Subscriber card) is a long stack of identical panels, now including a
  prepaid card, balance actions, product actions, order watch, history and commands.
- Dialogs grew organically (multi-step forms, confirmations, previews) without one dialog
  pattern.
- Status colours were extended ad hoc for statuses the first design did not cover.

**Goal:** make it look like a product designer planned the whole system: a coherent visual
language, clear hierarchy and a few memorable details. Not a new product. Keep what
works in the first handoff: monospaced tabular numbers, the right-aligned amounts, the real
minus sign, ISO dates, the restrained palette and the compact density.

## Hard constraints (the implementation depends on them)

- Desktop only, **minimum width 1280 px**. No mobile layout is needed.
- Light theme required. **A dark theme is welcome if it falls out of the token system**,
  but it is not required.
- Fonts are self-hosted: **Golos Text** (UI) and **JetBrains Mono** (numbers, ids, dates).
  Propose a change only with a strong reason; any font must be available on npm/@fontsource.
- Implemented in **React + plain CSS with custom-property tokens**. No Tailwind and no
  component library. Deliver tokens and component specs that map onto CSS variables.
- Numbers: JetBrains Mono, `tabular-nums`, `1,250.40`, always 2 decimals, the minus sign is U+2212,
  negatives in a danger colour, the currency code smaller and muted after the amount.
  Dates: `YYYY-MM-DD`, times `HH:MM:SS`, always in the business time zone shown in the top bar.
- Every list has four states: loading skeleton, error (the failed call + Retry), empty
  (copy + optional action), no results (Reset filters).
- Every write dialog has the states idle / busy / refused (the server's reason shown, input kept)
  / **"Unsure whether it was saved"** (network or 5xx, with Refresh). Some writes are final
  (a balance transfer) and need an explicit confirmation step.
- Accessible: visible focus, contrast AA, status never shown by colour alone.

## Screens (★ = design these in full; the rest must be derivable from the system)

Sidebar sections and screens:

- **Customers**
  - ★ **Subscribers**: list of billing accounts (name, display id `BA-1A2B3C4D`, status, mode
    postpaid/prepaid, balance, cycle day, products). Filters: status, mode, search.
  - ★ **Subscriber card**: the core screen, open most of the day. It holds the account header
    (status, mode, balance, credit limit, cycle day) and actions (Edit, Record payment, Add
    product). For a **prepaid** account: ledger balance, available, reserved, paid-through
    date, shortfall, and the actions Top up / Adjust / Hold / Transfer. Below that: active
    products with their actions (suspend, resume, change offering, terminate) and pending
    changes, orders in progress, charge history, transaction history, bills, payments and
    balance-action history. **This one needs a real information architecture**
    (tabs or sections, what is above the fold, what is secondary), not only styling.
  - Parties: people and organisations behind customers.
- **Products**
  - ★ Product catalog: offerings with lifecycle In Design → Active → Retired, prices,
    recurrence. Detail dialog with lifecycle actions and Change price.
  - Products, Orders (global list with drafts, cancel and order details), Numbers (subscriber
    number inventory: free, reserved, assigned).
- **Billing**
  - ★ **Billing runs**: nightly run at 00:30. Latest run (running / ok / failed, progress), history,
    per-account outcomes, Retry, Catch up to a date. The sidebar item shows a red "failed"
    badge when the latest run failed.
  - Prepaid commands (internal queue: status, retries), Accounts (financial accounts),
    Bills, Payments (incl. refunds), Balances (prepaid buckets), Billing settings.
- **System**: Integrations (event hub subscriptions, system panel), Users (operators, roles).
- Sign-in and change-password pages.

Top bar: breadcrumbs on the left. On the right, always visible: **business date** and **time
zone** (the system clock can differ from the wall clock).

Dialogs to design as patterns: ★ **Add product** (offering picker, number claim, start date,
first-charge preview with proration, and for prepaid: required / available / shortfall),
★ **Transfer balance** (multi-step: by number → amount → final confirmation → progress
polling), and a simple one-field form (Record payment).

## What I want back

1. **A design system**: a type scale (few steps, named by role), a spacing scale, a colour palette
   with semantic roles (surface, ink levels, primary, positive/negative/warning/info), a status
   palette covering the statuses below, radii, elevation, focus, density rules for tables.
2. **Components**: sidebar, top bar, page header (title + count + primary action + filters),
   table (header, row, hover, selected, amount cell, id cell, status cell, row actions, footer
   and pagination), panels and key-value blocks, a stat/summary block (balance, available,
   reserved), tabs, buttons (primary / secondary / danger / quiet), inputs, select, segmented
   control, date field, money field, pill, badge, toast, modal (sizes, multi-step, confirm),
   the four list states and the four write states, inline notes and warnings.
3. **The ★ screens** at 1280 px and at 1600 px, using realistic data (below).
4. A short rationale: the hierarchy each screen uses and why.

Statuses to cover: account Active / Suspended / Closed; offering In Design / Active / Retired;
product created / active / pendingTerminate / suspended / terminated / cancelled; order
acknowledged / inProgress / completed / failed / cancelled / draft; run running / completed /
failed; command queued / running / succeeded / failed (technical, retryable).

## Avoid (the "AI-generated" look)

- Big rounded cards with soft drop shadows everywhere, gradients, glassmorphism, purple accents.
- Dashboard tiles with huge numbers and icons that say nothing.
- Emoji, decorative icons on every label, an illustration in every empty state.
- Everything given the same visual weight, with the same padding and border.
- Airy marketing spacing: this is a dense operator tool, not a landing page.
- Centered layouts and narrow content columns on a 1600 px screen.

Think Linear, Stripe Dashboard, Bloomberg-lite: precise, quiet and dense, with confident typography
and colour used only where it carries meaning (money sign, status, failure, the one primary
action).

## Sample data

Currency EUR. Business date 2026-10-26, time zone UTC+3.
Subscribers: "Ivan Petrov" BA-3F9A12C0, Active, postpaid, balance −42.10, cycle day 1, 2 products;
"Nordwind Logistics OÜ" BA-81D0E7B4, Active, postpaid, credit limit 500.00, balance −1,250.40;
"Maria Sokolova" BA-C2290A5E, Active, prepaid, ledger 18.50, reserved 5.00, available 13.50,
paid through 2026-11-14; "Old Account" BA-0B11D3F2, Closed, balance 0.00.
Offerings: Mobile S (MOB-S, 9.90/month), Mobile L (MOB-L, 24.90/month), Data 10 GB
(DATA-10, 7.50/month, In Design), Prepaid Daily (PRE-D, 0.49/day).
Numbers: +372 5123 4567 style.
