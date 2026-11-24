# Tidewell redesign release evidence

Release record dated 2026-11-24 for #460, which finishes the Tidewell redesign that #452
started (decisions T1-T12 in [frontend.md](decisions/frontend.md#tidewell-visual-redesign-designed-2026-11-24-452)).
It is visual only: no request, body, route or polling change, and
`tests/internal/test_ui_requests.py` is untouched. The reference is the handoff in
`design-ref/design_handoff_tidewell/` (README "Visual foundations", `components/*.jsx`
and their cards, `guidelines/*.card.html`, `ui_kits/app/`).

## 1. Build

| Check | Result |
|---|---|
| `make web-lint` | passed (types, eslint, prettier) |
| `make web-test` | 80 files, 1136 tests passed, UTC and America/New_York |
| `make web-e2e` | 13 passed, 0 failed, 0 skipped, on the seeded task database |
| `make web-shots` | passed; 169 PNGs in `frontend/.shots/` (not committed) |

The shots now run with `reducedMotion: 'reduce'`: Tidewell's overlays fade in over
320 ms, and a capture taken during the fade showed half-transparent dialogs. Under reduced
motion every animation and transition finishes at once (the rule is in `ui.css`, so users
who ask for reduced motion get the same).

## 2. Shots reviewed

All 169 shots (light and dark, 1280 and 1600 px) were read against the Tidewell README
and component specs. Tidewell has no artboards of this product, so the check is the
system's rules, not pixel match: warm sand neutrals with no cool greys, one tide accent
(primary button, focus ring, checked controls, active tab underline, stepper), hairlines
before shadows, serif page and dialog titles and empty-state lines, sans UI text, mono
ids, amounts and eyebrows, Tidewell radii, and in dark mode legibility and token use (the
dark theme is derived, T8). Every full-page capture at 1280 px is exactly 1280 px wide, so
nothing scrolls horizontally.

| group | shots | result |
|---|---|---|
| Shell: sidebar, breadcrumbs, business-date chip, operator footer | every page | sand-100 panel with a hairline, BSS wordmark in Newsreader without a mark (T9), active item on the card colour with a hairline, chevron breadcrumbs, pill business-date chip, `Avatar` in the footer |
| Lists: subscribers, catalog (5 tabs), orders, products, numbers, bills, payments, balances (2), prepaid commands, parties, financial accounts, users, integrations, billing runs (2), billing settings | 4 each | pass; tables, segmented filters and status pills through the restyled primitives |
| Subscriber card: prepaid and postpaid, every tab and the row menu | 4 each | pass; tide underline tabs, hairline rules above Active products and Recent activity (were ink), drawn disclosure chevron for Self-care login, link-styled renewal history |
| Dialogs: add product (2), new subscriber, edit, offering, category, specification, order (2), refused order, payment detail, record payment and refund, money (3), transfer (4), subscriber number (2), financial account, users, account total | 4 each | pass; 3 px blurred scrim, 20 px radius with a hairline, 24 px padding, no rule under the header, Lucide close button, footer on the page colour |
| Catalog detail and its menu | 4 each | pass; the lifecycle stepper marks the current step in tide (was ink) |
| `/ui-kit` | 4 | every primitive and state: buttons (four variants, small, disabled, busy), fields, checkbox/radio/switch states, icon buttons, avatars, combobox, every D4 status pill, tags/flags/badges, amounts and ids, segmented, tabs and menu, tables and list states, notes, toast, dialogs and write states |

Fixes made during the review: the shots' reduced motion; the disabled checkbox no longer
dims twice; the New user dialog puts Cancel before Save like every other dialog.

## 3. What changed

- Primitives (slice 2): see the frontend.md progress line. New: `IconButton`, `Avatar`,
  `Radio`, `Switch`; the checkbox keeps its native input with a Lucide tick drawn over it.
- Screens (slice 3): Lucide replaces the remaining inline SVG and glyphs (subscriber search,
  the remove-filter crosses, the default row-menu trigger, Back to subscribers, the `+` of
  the New buttons, the dialog close, the list error mark). StatusPill keeps its 8 D4 glyph
  families. The care portal takes the admin's radii, focus ring, motion and serif titles.
- Voice: "+ New …" buttons are "New …" with a plus icon; the product row menu is named
  "Actions" (was "Actions ▾"). The copy already had no exclamation marks or "Submit"/"OK"
  buttons; "Number Transfer PIN" stays, as the regulatory term the API uses.
- Tokens (slice 4): the Ledger aliases nothing used are deleted (`--color-highlight`,
  `--color-highlight-tint`, `--color-link-underline`, `--color-rule`,
  `--header-row-height`, `--inspector-width`, `--space-2xs`); the style guard passes.

Not in this PR: the CLAUDE.md "Current state" line that names Tidewell, because merging a
CLAUDE.md change needs the human's approval at the PR's head.
